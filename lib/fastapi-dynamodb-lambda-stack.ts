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
      timeout: cdk.Duration.seconds(30),
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
        timeout: cdk.Duration.seconds(30),
        environment: {
          TABLE_NAME: table_prod.tableName,
          AWS_REGION_NAME: this.region,
          AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
          AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    // Add Cognito permissions to prod Lambda
    fastApiLambda_prod.addToRolePolicy(cognitoPolicy);
    table_prod.grantReadWriteData(fastApiLambda_prod);
    table_movies.grantReadWriteData(fastApiLambda_prod);

    // API Gateway for FastAPI Lambda - Production
    const api_prod = new apigateway.LambdaRestApi(this, "FastApiGateway", {
      handler: fastApiLambda_prod,
      proxy: true,
      description: "APIGateway for FastAPI Lambda DDB docker image test service",
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
        timeout: cdk.Duration.seconds(30),
        environment: {
          TABLE_NAME: table_dev.tableName,
          AWS_REGION_NAME: this.region,
          AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
          AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
        },
        architecture: lambda.Architecture.X86_64,
      }
    );

    // Add Cognito permissions to dev Lambda
    fastApiLambda_dev.addToRolePolicy(cognitoPolicy);
    table_dev.grantReadWriteData(fastApiLambda_dev);
    table_movies.grantReadWriteData(fastApiLambda_dev);

    // API Gateway for FastAPI Lambda - Development
    const api_dev = new apigateway.LambdaRestApi(this, "FastApiGatewayDev", {
      handler: fastApiLambda_dev,
      proxy: true,
      description: "APIGateway for FastAPI Lambda DDB docker image test service",
    });

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
  }
}
