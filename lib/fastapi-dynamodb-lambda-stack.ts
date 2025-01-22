import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import { RemovalPolicy } from 'aws-cdk-lib';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import { ApiGatewayDomain } from 'aws-cdk-lib/aws-route53-targets';
import * as path from 'path';

export class FastapiDynamodbLambdaStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Production DynamoDB Table
    const prodTable = new dynamodb.Table(this, 'ProdTable', {
      partitionKey: { name: 'mealID', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.RETAIN, // Recommended for production
    });

    // Development DynamoDB Table
    const devTable = new dynamodb.Table(this, 'DevTable', {
      partitionKey: { name: 'mealID', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.DESTROY, // Not recommended for production
    });

    // Production Lambda Function
    const prodLambda = new lambda.DockerImageFunction(this, 'ProdFastApiFunction', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../api')
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      environment: {
        TABLE_NAME: prodTable.tableName,
        ENV: 'prod'
      },
    });

    // Development Lambda Function
    const devLambda = new lambda.DockerImageFunction(this, 'DevFastApiFunction', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../api')
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      environment: {
        TABLE_NAME: devTable.tableName,
        ENV: 'dev'
      },
    });

    // Grant permissions to Lambda functions
    prodTable.grantReadWriteData(prodLambda);
    devTable.grantReadWriteData(devLambda);

    // Production API Gateway
    const prodApi = new apigateway.LambdaRestApi(this, 'ProdFastApiGateway', {
      handler: prodLambda,
      proxy: true,
    });

    // Development API Gateway
    const devApi = new apigateway.LambdaRestApi(this, 'DevFastApiGateway', {
      handler: devLambda,
      proxy: true,
    });

    // Custom Domain Setup for Production
    const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName: 'adamsulemanji.com',
    });

    const certificate = new acm.Certificate(this, 'CustomDomainCertificate', {
      domainName: 'api.fast.adamsulemanji.com',
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    const domainName = new apigateway.DomainName(this, 'CustomDomain', {
      domainName: 'api.fast.adamsulemanji.com',
      certificate,
      endpointType: apigateway.EndpointType.EDGE,
      securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
    });

    new apigateway.BasePathMapping(this, 'BasePathMapping', {
      domainName,
      restApi: prodApi,
      basePath: ''
    });

    new route53.ARecord(this, 'CustomDomainAliasRecord', {
      zone: hostedZone,
      recordName: 'api.fast',
      target: route53.RecordTarget.fromAlias(new ApiGatewayDomain(domainName)),
    });

    // Outputs
    new cdk.CfnOutput(this, 'ProdApiUrl', {
      value: prodApi.url,
      description: 'Default Invoke URL for the Production FastAPI service',
    });

    new cdk.CfnOutput(this, 'DevApiUrl', {
      value: devApi.url,
      description: 'Default Invoke URL for the Development FastAPI service',
    });

    new cdk.CfnOutput(this, 'CustomDomainUrl', {
      value: `https://${domainName.domainName}/`,
      description: 'Custom domain for the Production FastAPI service',
    });
  }
}
