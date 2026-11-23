import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as path from "path";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as iam from "aws-cdk-lib/aws-iam";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sfn from "aws-cdk-lib/aws-stepfunctions";
import * as sfnTasks from "aws-cdk-lib/aws-stepfunctions-tasks";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { RemovalPolicy } from "aws-cdk-lib";
import { ApiGatewayDomain } from "aws-cdk-lib/aws-route53-targets";

export class FastapiDynamodbLambdaStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ========================================================================
    // DynamoDB Tables
    // ========================================================================
    const table_dev = new dynamodb.Table(this, "MyTableDev", {
      tableName: "MyTable-dev",
      partitionKey: {
        name: "mealID",
        type: dynamodb.AttributeType.STRING,
      },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const table_prod = new dynamodb.Table(this, "MyTableProd", {
      tableName: "MyTable-prod",
      partitionKey: {
        name: "mealID",
        type: dynamodb.AttributeType.STRING,
      },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const table_movies = new dynamodb.Table(this, "MoviesTable", {
      tableName: "movies",
      partitionKey: {
        name: "username",
        type: dynamodb.AttributeType.STRING,
      },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const table_movies_jobs = new dynamodb.Table(this, "MoviesJobsTable", {
      tableName: "movies-jobs",
      partitionKey: {
        name: "job_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "expires_at",
      removalPolicy: RemovalPolicy.RETAIN,
    });

    table_movies_jobs.addGlobalSecondaryIndex({
      indexName: "username-created_at-index",
      partitionKey: {
        name: "username",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "created_at",
        type: dynamodb.AttributeType.NUMBER,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const table_movies_poster_jobs = new dynamodb.Table(this, "MoviesPosterJobsTable", {
      tableName: "movies-poster-jobs",
      partitionKey: {
        name: "job_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "expires_at",
      removalPolicy: RemovalPolicy.RETAIN,
    });

    table_movies_poster_jobs.addGlobalSecondaryIndex({
      indexName: "username-created_at-index",
      partitionKey: {
        name: "username",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: "created_at",
        type: dynamodb.AttributeType.NUMBER,
      },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    const moviesScrapeDlq = new sqs.Queue(this, "MoviesScrapeDlq", {
      retentionPeriod: cdk.Duration.days(14),
    });

    const moviesScrapeQueue = new sqs.Queue(this, "MoviesScrapeQueue", {
      visibilityTimeout: cdk.Duration.minutes(16),
      retentionPeriod: cdk.Duration.days(4),
      deadLetterQueue: {
        queue: moviesScrapeDlq,
        maxReceiveCount: 3,
      },
    });

    const moviesPosterTempBucket = new s3.Bucket(this, "MoviesPosterTempBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(3),
        },
      ],
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const moviesPosterOutputBucket = new s3.Bucket(this, "MoviesPosterOutputBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // Holds movie lists that are too large for a single DynamoDB item (400 KB);
    // the movies table keeps the metadata and a pointer to the object.
    const moviesCacheBucket = new s3.Bucket(this, "MoviesCacheBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // ========================================================================
    // Strava
    // ========================================================================
    // Cached recent runs, keyed by athlete id.
    const table_strava = new dynamodb.Table(this, "StravaTable", {
      tableName: "strava",
      partitionKey: {
        name: "athlete_id",
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    // JSON: {client_id, client_secret} set by hand; the API writes the OAuth
    // tokens (access_token, refresh_token, expires_at) into it after /strava/auth.
    const stravaSecret = new secretsmanager.Secret(this, "StravaCredentials", {
      secretName: "strava/credentials",
      description: "Strava API app credentials and OAuth tokens",
      secretStringValue: cdk.SecretValue.unsafePlainText("{}"),
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const stravaEnvironment = {
      STRAVA_TABLE_NAME: table_strava.tableName,
      STRAVA_SECRET_ID: stravaSecret.secretName,
      STRAVA_ATHLETE_ID: "109469044",
    };

    // ========================================================================
    // Cognito User Pool
    // ========================================================================
    const userPool = new cognito.UserPool(this, "UserPool", {
      selfSignUpEnabled: true,
      autoVerify: { email: true },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const userPoolClient = new cognito.UserPoolClient(this, "UserPoolClient", {
      userPool,
      authFlows: {
        userPassword: true,
        userSrp: true,
      },
      generateSecret: false,
    });

    const cognitoPolicy = new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        "cognito-idp:ListUserPools",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:ListUsers",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminInitiateAuth",
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminUpdateUserAttributes",
        "cognito-idp:AdminDeleteUser",
      ],
      resources: ["*"],
    });

    // ========================================================================
    // Go API Lambda
    // ========================================================================
    const goApiLambda = new lambda.DockerImageFunction(this, "GoApiFunction", {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, "../go_api"),
        {
          buildArgs: {
            "--platform": "linux/amd64",
          },
        }
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(300),
      environment: {
        TABLE_NAME: table_prod.tableName,
        AWS_REGION_NAME: this.region,
        STAGE: "prod",
      },
      architecture: lambda.Architecture.X86_64,
    });

    // Grant permissions to the Go API Lambda
    table_prod.grantReadWriteData(goApiLambda);
    table_movies.grantReadWriteData(goApiLambda);

    // API Gateway for Go API
    const goApi = new apigateway.LambdaRestApi(this, "GoApiGateway", {
      handler: goApiLambda,
      proxy: true,
      description: "API Gateway for Go Lambda service",
    });

    // ========================================================================
    // FastAPI Lambda - Production
    // ========================================================================
    const fastApiLambda_prod = new lambda.DockerImageFunction(
      this,
      "FastApiFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
          }
        ),
        memorySize: 512,
        timeout: cdk.Duration.seconds(300),
        environment: {
          TABLE_NAME: table_prod.tableName,
          AWS_REGION_NAME: this.region,
          AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
          AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
          MOVIES_TABLE_NAME: table_movies.tableName,
          MOVIES_CACHE_BUCKET_NAME: moviesCacheBucket.bucketName,
          MOVIES_JOBS_TABLE_NAME: table_movies_jobs.tableName,
          MOVIES_JOBS_USERNAME_INDEX: "username-created_at-index",
          MOVIES_SCRAPE_QUEUE_URL: moviesScrapeQueue.queueUrl,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
          MOVIES_POSTER_JOBS_USERNAME_INDEX: "username-created_at-index",
          MOVIES_POSTER_OUTPUT_BUCKET_NAME: moviesPosterOutputBucket.bucketName,
          MOVIES_POSTER_URL_EXPIRATION_SECONDS: "604800",
          ...stravaEnvironment,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    // Add Cognito permissions to prod Lambda
    fastApiLambda_prod.addToRolePolicy(cognitoPolicy);
    table_prod.grantReadWriteData(fastApiLambda_prod);
    table_movies.grantReadWriteData(fastApiLambda_prod);
    moviesCacheBucket.grantReadWrite(fastApiLambda_prod);
    table_movies_jobs.grantReadWriteData(fastApiLambda_prod);
    table_movies_poster_jobs.grantReadWriteData(fastApiLambda_prod);
    moviesScrapeQueue.grantSendMessages(fastApiLambda_prod);
    moviesPosterOutputBucket.grantRead(fastApiLambda_prod);
    table_strava.grantReadWriteData(fastApiLambda_prod);
    stravaSecret.grantRead(fastApiLambda_prod);
    stravaSecret.grantWrite(fastApiLambda_prod);

    // API Gateway for FastAPI Lambda - Production
    const api_prod = new apigateway.LambdaRestApi(this, "FastApiGateway", {
      handler: fastApiLambda_prod,
      proxy: true,
      description: "APIGateway for FastAPI Lambda DDB docker image test service",
      defaultCorsPreflightOptions: {
        allowOrigins: [
          "https://adamsulemanji.com",
          "https://www.adamsulemanji.com",
          "https://adamsulemanji.vercel.app"
        ],
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: apigateway.Cors.DEFAULT_HEADERS.concat([
          'Content-Type',
          'X-Amz-Date',
          'Authorization',
          'X-Api-Key',
          'X-Amz-Security-Token',
          'X-Amz-User-Agent'
        ]),
        allowCredentials: true,
      },
    });

    // ========================================================================
    // FastAPI Lambda - Development
    // ========================================================================
    const fastApiLambda_dev = new lambda.DockerImageFunction(
      this,
      "FastApiFunctionDev",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
          }
        ),
        memorySize: 512,
        timeout: cdk.Duration.seconds(300),
        environment: {
          TABLE_NAME: table_dev.tableName,
          AWS_REGION_NAME: this.region,
          AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
          AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
          MOVIES_TABLE_NAME: table_movies.tableName,
          MOVIES_CACHE_BUCKET_NAME: moviesCacheBucket.bucketName,
          MOVIES_JOBS_TABLE_NAME: table_movies_jobs.tableName,
          MOVIES_JOBS_USERNAME_INDEX: "username-created_at-index",
          MOVIES_SCRAPE_QUEUE_URL: moviesScrapeQueue.queueUrl,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
          MOVIES_POSTER_JOBS_USERNAME_INDEX: "username-created_at-index",
          MOVIES_POSTER_OUTPUT_BUCKET_NAME: moviesPosterOutputBucket.bucketName,
          MOVIES_POSTER_URL_EXPIRATION_SECONDS: "604800",
          ...stravaEnvironment,
        },
        architecture: lambda.Architecture.X86_64, 
      }
    );

    // Add Cognito permissions to dev Lambda
    fastApiLambda_dev.addToRolePolicy(cognitoPolicy);
    table_dev.grantReadWriteData(fastApiLambda_dev);
    table_movies.grantReadWriteData(fastApiLambda_dev);
    moviesCacheBucket.grantReadWrite(fastApiLambda_dev);
    table_movies_jobs.grantReadWriteData(fastApiLambda_dev);
    table_movies_poster_jobs.grantReadWriteData(fastApiLambda_dev);
    moviesScrapeQueue.grantSendMessages(fastApiLambda_dev);
    moviesPosterOutputBucket.grantRead(fastApiLambda_dev);
    table_strava.grantReadWriteData(fastApiLambda_dev);
    stravaSecret.grantRead(fastApiLambda_dev);
    stravaSecret.grantWrite(fastApiLambda_dev);

    // API Gateway for FastAPI Lambda - Development
    const api_dev = new apigateway.LambdaRestApi(this, "FastApiGatewayDev", {
      handler: fastApiLambda_dev,
      proxy: true,
      description: "APIGateway for FastAPI Lambda DDB docker image test service",
      defaultCorsPreflightOptions: {
        allowOrigins: [
          "https://adamsulemanji.com",
          "https://www.adamsulemanji.com",
          "https://adamsulemanji.vercel.app",
          "http://localhost:3000",
          "http://localhost:8000"
        ],
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: apigateway.Cors.DEFAULT_HEADERS.concat([
          'Content-Type',
          'X-Amz-Date',
          'Authorization',
          'X-Api-Key',
          'X-Amz-Security-Token',
          'X-Amz-User-Agent'
        ]),
        allowCredentials: true,
      },
    });

    // ========================================================================
    // Movies Worker Lambda (SQS Consumer)
    // ========================================================================
    const moviesWorkerLambda = new lambda.DockerImageFunction(
      this,
      "MoviesWorkerFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
            cmd: ["worker.handler"],
          }
        ),
        memorySize: 1024,
        timeout: cdk.Duration.minutes(15),
        environment: {
          AWS_REGION_NAME: this.region,
          MOVIES_TABLE_NAME: table_movies.tableName,
          MOVIES_CACHE_BUCKET_NAME: moviesCacheBucket.bucketName,
          MOVIES_JOBS_TABLE_NAME: table_movies_jobs.tableName,
          MOVIES_JOBS_USERNAME_INDEX: "username-created_at-index",
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    moviesWorkerLambda.addEventSource(
      new lambdaEventSources.SqsEventSource(moviesScrapeQueue, {
        batchSize: 1,
        reportBatchItemFailures: true,
      })
    );

    table_movies.grantReadWriteData(moviesWorkerLambda);
    moviesCacheBucket.grantReadWrite(moviesWorkerLambda);
    table_movies_jobs.grantReadWriteData(moviesWorkerLambda);
    moviesScrapeQueue.grantConsumeMessages(moviesWorkerLambda);

    // ========================================================================
    // Movies Poster Workflow Lambdas
    // ========================================================================
    const posterBuildManifestLambda = new lambda.DockerImageFunction(
      this,
      "PosterBuildManifestFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
            cmd: ["poster_worker.build_manifest_handler"],
          }
        ),
        memorySize: 1024,
        timeout: cdk.Duration.minutes(15),
        environment: {
          AWS_REGION_NAME: this.region,
          MOVIES_TABLE_NAME: table_movies.tableName,
          MOVIES_CACHE_BUCKET_NAME: moviesCacheBucket.bucketName,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
          MOVIES_POSTER_TEMP_BUCKET_NAME: moviesPosterTempBucket.bucketName,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    const posterProcessItemLambda = new lambda.DockerImageFunction(
      this,
      "PosterProcessItemFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
            cmd: ["poster_worker.process_poster_item_handler"],
          }
        ),
        memorySize: 1024,
        timeout: cdk.Duration.minutes(2),
        environment: {
          AWS_REGION_NAME: this.region,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
          MOVIES_POSTER_TEMP_BUCKET_NAME: moviesPosterTempBucket.bucketName,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    const posterRenderLambda = new lambda.DockerImageFunction(
      this,
      "PosterRenderFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
            cmd: ["poster_worker.render_poster_handler"],
          }
        ),
        memorySize: 3008,
        timeout: cdk.Duration.minutes(15),
        ephemeralStorageSize: cdk.Size.gibibytes(3),
        environment: {
          AWS_REGION_NAME: this.region,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
          MOVIES_POSTER_TEMP_BUCKET_NAME: moviesPosterTempBucket.bucketName,
          MOVIES_POSTER_OUTPUT_BUCKET_NAME: moviesPosterOutputBucket.bucketName,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    const posterFailLambda = new lambda.DockerImageFunction(
      this,
      "PosterFailFunction",
      {
        code: lambda.DockerImageCode.fromImageAsset(
          path.join(__dirname, "../api"),
          {
            buildArgs: {
              "--platform": "linux/amd64",
            },
            cmd: ["poster_worker.fail_poster_job_handler"],
          }
        ),
        memorySize: 512,
        timeout: cdk.Duration.minutes(1),
        environment: {
          AWS_REGION_NAME: this.region,
          MOVIES_POSTER_JOBS_TABLE_NAME: table_movies_poster_jobs.tableName,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    table_movies.grantReadData(posterBuildManifestLambda);
    moviesCacheBucket.grantRead(posterBuildManifestLambda);
    table_movies_poster_jobs.grantReadWriteData(posterBuildManifestLambda);
    table_movies_poster_jobs.grantReadWriteData(posterProcessItemLambda);
    table_movies_poster_jobs.grantReadWriteData(posterRenderLambda);
    table_movies_poster_jobs.grantReadWriteData(posterFailLambda);

    moviesPosterTempBucket.grantReadWrite(posterBuildManifestLambda);
    moviesPosterTempBucket.grantReadWrite(posterProcessItemLambda);
    moviesPosterTempBucket.grantReadWrite(posterRenderLambda);
    moviesPosterOutputBucket.grantPut(posterRenderLambda);

    const buildPosterManifestTask = new sfnTasks.LambdaInvoke(this, "BuildPosterManifestTask", {
      lambdaFunction: posterBuildManifestLambda,
      payload: sfn.TaskInput.fromObject({
        "job_id.$": "$.job_id",
        "username.$": "$.username",
        "movie_count.$": "$.movie_count",
        "bootstrap_movies.$": "$.bootstrap_movies",
      }),
      payloadResponseOnly: true,
    });

    const processPosterItemTask = new sfnTasks.LambdaInvoke(this, "ProcessPosterItemTask", {
      lambdaFunction: posterProcessItemLambda,
      payload: sfn.TaskInput.fromObject({
        "job_id.$": "$.job_id",
        "username.$": "$.username",
        "movie_count.$": "$.movie_count",
        "total_items.$": "$.total_items",
        "movie.$": "$.movie",
      }),
      payloadResponseOnly: true,
    });

    const processPosterMap = new sfn.Map(this, "ProcessPosterMap", {
      itemsPath: sfn.JsonPath.stringAt("$.movies"),
      // Results are not needed downstream (the render step lists tiles in S3);
      // discarding them keeps the state under Step Functions' 256 KB limit.
      resultPath: sfn.JsonPath.DISCARD,
      itemSelector: {
        "job_id.$": "$.job_id",
        "username.$": "$.username",
        "movie_count.$": "$.movie_count",
        "total_items.$": "$.total_items",
        "movie.$": "$$.Map.Item.Value",
      },
      maxConcurrency: 20,
    });
    processPosterMap.itemProcessor(processPosterItemTask);

    const renderPosterTask = new sfnTasks.LambdaInvoke(this, "RenderPosterTask", {
      lambdaFunction: posterRenderLambda,
      payload: sfn.TaskInput.fromObject({
        "job_id.$": "$.job_id",
        "username.$": "$.username",
        "movie_count.$": "$.movie_count",
        "total_items.$": "$.total_items",
      }),
      payloadResponseOnly: true,
    });

    const failPosterJobTask = new sfnTasks.LambdaInvoke(this, "FailPosterJobTask", {
      lambdaFunction: posterFailLambda,
      payload: sfn.TaskInput.fromObject({
        "job_id.$": "$.job_id",
        "error.$": "$.workflow_error.Error",
        "cause.$": "$.workflow_error.Cause",
      }),
      payloadResponseOnly: true,
    });

    const posterWorkflowFailed = new sfn.Fail(this, "PosterWorkflowFailed", {
      cause: "Poster workflow failed",
    });
    const posterFailureChain = sfn.Chain.start(failPosterJobTask).next(posterWorkflowFailed);

    buildPosterManifestTask.addCatch(posterFailureChain, {
      resultPath: "$.workflow_error",
    });
    processPosterMap.addCatch(posterFailureChain, {
      resultPath: "$.workflow_error",
    });
    renderPosterTask.addCatch(posterFailureChain, {
      resultPath: "$.workflow_error",
    });

    const moviesPosterStateMachine = new sfn.StateMachine(this, "MoviesPosterStateMachine", {
      definitionBody: sfn.DefinitionBody.fromChainable(
        sfn.Chain.start(buildPosterManifestTask).next(processPosterMap).next(renderPosterTask)
      ),
      timeout: cdk.Duration.minutes(30),
    });

    moviesPosterStateMachine.grantStartExecution(fastApiLambda_prod);
    moviesPosterStateMachine.grantStartExecution(fastApiLambda_dev);
    fastApiLambda_prod.addEnvironment("MOVIES_POSTER_STATE_MACHINE_ARN", moviesPosterStateMachine.stateMachineArn);
    fastApiLambda_dev.addEnvironment("MOVIES_POSTER_STATE_MACHINE_ARN", moviesPosterStateMachine.stateMachineArn);

    // ========================================================================
    // Domain Configuration
    // ========================================================================
    const hostedZone = route53.HostedZone.fromLookup(this, "HostedZone", {
      domainName: "adamsulemanji.com",
    });

    // ========================================================================
    // FastAPI Custom Domain
    // ========================================================================
    const certificate = new acm.Certificate(this, "CustomDomainCertificate", {
      domainName: "api.fast.adamsulemanji.com",
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    const domainName = new apigateway.DomainName(this, "CustomDomain", {
      domainName: "api.fast.adamsulemanji.com",
      certificate,
      endpointType: apigateway.EndpointType.EDGE,
      securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
    });

    new apigateway.BasePathMapping(this, "BasePathMapping", {
      domainName,
      restApi: api_prod,
      basePath: "",
    });

    new route53.ARecord(this, "CustomDomainAliasRecord", {
      zone: hostedZone,
      recordName: "api.fast",
      target: route53.RecordTarget.fromAlias(new ApiGatewayDomain(domainName)),
    });

    // ========================================================================
    // Go API Custom Domain
    // ========================================================================
    const goCertificate = new acm.Certificate(this, "GoApiCertificate", {
      domainName: "go.fast.adamsulemanji.com",
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    const goDomainName = new apigateway.DomainName(this, "GoCustomDomain", {
      domainName: "go.fast.adamsulemanji.com",
      certificate: goCertificate,
      endpointType: apigateway.EndpointType.EDGE,
      securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
    });

    new apigateway.BasePathMapping(this, "GoBasePathMapping", {
      domainName: goDomainName,
      restApi: goApi,
      basePath: "",
    });

    new route53.ARecord(this, "GoCustomDomainAliasRecord", {
      zone: hostedZone,
      recordName: "go.fast",
      target: route53.RecordTarget.fromAlias(new ApiGatewayDomain(goDomainName)),
    });

    // ========================================================================
    // Outputs
    // ========================================================================
    
    // FastAPI Outputs
    new cdk.CfnOutput(this, "CustomDomainUrl", {
      value: `https://${domainName.domainName}/`,
      description: "Custom domain for the FastAPI service",
    });
    
    new cdk.CfnOutput(this, "ApiUrlProd", {
      value: api_prod.url,
      description: "Default Invoke URL for the FastAPI service",
    });

    new cdk.CfnOutput(this, "ApiUrlDev", {
      value: api_dev.url,
      description: "Default Invoke URL for the FastAPI service",
    });
    
    // Go API Outputs
    new cdk.CfnOutput(this, "GoCustomDomainUrl", {
      value: `https://${goDomainName.domainName}/`,
      description: "Custom domain for the Go API service",
    });
    
    new cdk.CfnOutput(this, "GoApiUrl", {
      value: goApi.url,
      description: "Default Invoke URL for the Go API service",
    });

    // Cognito Outputs
    new cdk.CfnOutput(this, "UserPoolId", {
      value: userPool.userPoolId,
      description: "The ID of the Cognito User Pool",
    });

    new cdk.CfnOutput(this, "UserPoolClientId", {
      value: userPoolClient.userPoolClientId,
      description: "The ID of the Cognito User Pool Client",
    });

    new cdk.CfnOutput(this, "MoviesJobsTableName", {
      value: table_movies_jobs.tableName,
      description: "DynamoDB table storing async movies job status",
    });

    new cdk.CfnOutput(this, "MoviesScrapeQueueUrl", {
      value: moviesScrapeQueue.queueUrl,
      description: "SQS queue URL for movies scrape jobs",
    });

    new cdk.CfnOutput(this, "MoviesPosterJobsTableName", {
      value: table_movies_poster_jobs.tableName,
      description: "DynamoDB table storing poster generation jobs",
    });

    new cdk.CfnOutput(this, "MoviesCacheBucketName", {
      value: moviesCacheBucket.bucketName,
      description: "S3 bucket storing movie lists offloaded from DynamoDB",
    });

    new cdk.CfnOutput(this, "MoviesPosterOutputBucketName", {
      value: moviesPosterOutputBucket.bucketName,
      description: "S3 bucket storing generated poster images",
    });

    new cdk.CfnOutput(this, "MoviesPosterStateMachineArn", {
      value: moviesPosterStateMachine.stateMachineArn,
      description: "Step Functions state machine for poster workflows",
    });
  }
}
