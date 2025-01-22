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

    
    const table = new dynamodb.Table(this, 'MyTable', {
      partitionKey: { name: 'mealID', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.DESTROY, // Not recommended for production
    });

    
    const fastApiLambda = new lambda.DockerImageFunction(this, 'FastApiFunction', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../api')
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      environment: {
        TABLE_NAME: table.tableName, 
      },
    });

    
    table.grantReadWriteData(fastApiLambda);

    
    const api = new apigateway.LambdaRestApi(this, 'FastApiGateway', {
      handler: fastApiLambda,
      proxy: true, 
    });

    // ------------------------------------------------------------------------------------------
    // Setup Custom Domain
    // ------------------------------------------------------------------------------------------
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
      restApi: api,
      basePath: ''
    });

    
    new route53.ARecord(this, 'CustomDomainAliasRecord', {
      zone: hostedZone,
      recordName: 'api.fast', 
      target: route53.RecordTarget.fromAlias(new ApiGatewayDomain(domainName)),
    });

    
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'Default Invoke URL for the FastAPI service',
    });

    
    new cdk.CfnOutput(this, 'CustomDomainUrl', {
      value: `https://${domainName.domainName}/`,
      description: 'Custom domain for the FastAPI service',
    });
  }
}
