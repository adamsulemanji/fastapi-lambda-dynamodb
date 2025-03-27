import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigateway from "aws-cdk-lib/aws-apigateway";
import { RemovalPolicy } from "aws-cdk-lib";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import { ApiGatewayDomain } from "aws-cdk-lib/aws-route53-targets";
import * as path from "path";
import * as cognito from "aws-cdk-lib/aws-cognito"; // Add this import

export class FastapiDynamodbLambdaStack extends cdk.Stack {
	constructor(scope: Construct, id: string, props?: cdk.StackProps) {
		super(scope, id, props);

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

		// Create a Cognito User Pool
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
			removalPolicy: RemovalPolicy.DESTROY, // Use RETAIN for production
		});

		// Create a User Pool Client
		const userPoolClient = new cognito.UserPoolClient(this, "UserPoolClient", {
			userPool,
			authFlows: {
				userPassword: true,
				userSrp: true,
			},
			generateSecret: false,
		});

		const fastApiLambda_prod = new lambda.DockerImageFunction(
			this,
			"FastApiFunction",
			{
				code: lambda.DockerImageCode.fromImageAsset(
					path.join(__dirname, "../api")
				),
				memorySize: 512,
				timeout: cdk.Duration.seconds(30),
				environment: {
					TABLE_NAME: table_prod.tableName,
					AWS_REGION_NAME: this.region,
					AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
					AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
				},
			}
		);

		table_prod.grantReadWriteData(fastApiLambda_prod);
		table_movies.grantReadWriteData(fastApiLambda_prod);

		const fastApiLambda_dev = new lambda.DockerImageFunction(
			this,
			"FastApiFunctionDev",
			{
				code: lambda.DockerImageCode.fromImageAsset(
					path.join(__dirname, "../api")
				),
				memorySize: 512,
				timeout: cdk.Duration.seconds(30),
				environment: {
					TABLE_NAME: table_dev.tableName,
					AWS_REGION_NAME: this.region,
					AWS_COGNITO_USER_POOL_ID: userPool.userPoolId,
					AWS_COGNITO_APP_CLIENT_ID: userPoolClient.userPoolClientId,
				},
			}
		);

		table_dev.grantReadWriteData(fastApiLambda_dev);
		table_movies.grantReadWriteData(fastApiLambda_dev);

		const api_prod = new apigateway.LambdaRestApi(this, "FastApiGateway", {
			handler: fastApiLambda_prod,
			proxy: true,
			description:
				"APIGateway for FastAPI Lambda DDB docker image test service",
		});

		const api_dev = new apigateway.LambdaRestApi(
			this,
			"FastApiGatewayDev",
			{
				handler: fastApiLambda_dev,
				proxy: true,
				description:
					"APIGateway for FastAPI Lambda DDB docker image test service",
			}
		);

		// --- The Custom Domain logic here would typically only be for 'prod' ---
		// But you can parameterize that as well, or skip it in dev.

		const hostedZone = route53.HostedZone.fromLookup(this, "HostedZone", {
			domainName: "adamsulemanji.com",
		});

		const certificate = new acm.Certificate(
			this,
			"CustomDomainCertificate",
			{
				domainName: "api.fast.adamsulemanji.com",
				validation: acm.CertificateValidation.fromDns(hostedZone),
			}
		);

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
			target: route53.RecordTarget.fromAlias(
				new ApiGatewayDomain(domainName)
			),
		});

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

		// Output the Cognito User Pool and Client IDs
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
