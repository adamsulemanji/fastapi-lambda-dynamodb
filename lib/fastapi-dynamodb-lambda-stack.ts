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

    // 1) Create a DynamoDB table
    const table = new dynamodb.Table(this, 'MyTable', {
      partitionKey: { name: 'mealID', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.DESTROY, // Not recommended for production
    });

    // 2) Create the Docker-based Lambda function
    const fastApiLambda = new lambda.DockerImageFunction(this, 'FastApiFunction', {
      code: lambda.DockerImageCode.fromImageAsset(
        path.join(__dirname, '../api')
      ),
      memorySize: 512,
      timeout: cdk.Duration.seconds(30),
      environment: {
        TABLE_NAME: table.tableName, // pass table name to your FastAPI code
      },
    });

    // 3) Grant the Lambda read/write access to the table
    table.grantReadWriteData(fastApiLambda);

    // 4) Create the REST API from this Lambda
    const api = new apigateway.LambdaRestApi(this, 'FastApiGateway', {
      handler: fastApiLambda,
      proxy: true, // forward all requests to FastAPI
    });

    // ------------------------------------------------------------------------------------------
    // 5) Setup Custom Domain
    // ------------------------------------------------------------------------------------------
    // 5a) Look up your Hosted Zone (make sure the domain in Route53 is "adamsulemanji.com")
    const hostedZone = route53.HostedZone.fromLookup(this, 'HostedZone', {
      domainName: 'adamsulemanji.com',
    });

    // 5b) Request a certificate in us-east-1 (for an edge-optimized API)
    // If your stack is NOT in us-east-1, you may need to either:
    //   1) Import an existing cert ARN from us-east-1
    //   2) Create a separate stack/construct in us-east-1 region just for this certificate
    // For simplicity, we'll assume this is in us-east-1 or you are fine with separate region logic.

    const certificate = new acm.Certificate(this, 'CustomDomainCertificate', {
      domainName: 'api.fast.adamsulemanji.com',
      validation: acm.CertificateValidation.fromDns(hostedZone),
    });

    // 5c) Create the custom domain in API Gateway
    const domainName = new apigateway.DomainName(this, 'CustomDomain', {
      domainName: 'api.fast.adamsulemanji.com',
      certificate,
      endpointType: apigateway.EndpointType.EDGE, // or REGIONAL
      securityPolicy: apigateway.SecurityPolicy.TLS_1_2,
    });

    // 5d) Map the custom domain to your API
    new apigateway.BasePathMapping(this, 'BasePathMapping', {
      domainName,
      restApi: api,
      basePath: '' // leave empty for root
    });

    // 5e) Create a Route53 DNS record to alias the custom domain to the API Gateway domain
    new route53.ARecord(this, 'CustomDomainAliasRecord', {
      zone: hostedZone,
      recordName: 'api.fast', // just the subdomain part (api.fast.adamsulemanji.com)
      target: route53.RecordTarget.fromAlias(new ApiGatewayDomain(domainName)),
    });

    // Output the API URL (this will be the default Amazon domain; 
    // you can also create an output for the custom domain, if desired)
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'Default Invoke URL for the FastAPI service',
    });

    // If you’d like to output the custom domain instead, you can do:
    new cdk.CfnOutput(this, 'CustomDomainUrl', {
      value: `https://${domainName.domainName}/`,
      description: 'Custom domain for the FastAPI service',
    });
  }
}
