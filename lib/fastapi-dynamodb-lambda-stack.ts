import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { aws_lambda as lambda, aws_dynamodb as dynamodb, aws_apigateway as apigateway } from 'aws-cdk-lib';
import { RemovalPolicy } from 'aws-cdk-lib';
import * as path from 'path';

export class FastapiDynamodbLambdaStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // 1) Create a DynamoDB table
    const table = new dynamodb.Table(this, 'MyTable', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
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

    // 4) Create an API Gateway to front the Lambda
    const api = new apigateway.LambdaRestApi(this, 'FastApiGateway', {
      handler: fastApiLambda,
      proxy: true, // forward all requests to FastAPI
    });

    // Output the API URL
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'Invoke URL for the FastAPI service',
    });
  }
}