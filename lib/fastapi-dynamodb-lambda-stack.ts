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
				},
			}
		);

		table_prod.grantReadWriteData(fastApiLambda_prod);

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
				},
			}
		);

		table_dev.grantReadWriteData(fastApiLambda_dev);

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
	}
}
