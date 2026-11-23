#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { FastapiDynamodbLambdaStack } from '../lib/fastapi-dynamodb-lambda-stack';
import { ApiPipelineStack } from '../lib/pipeline-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION,
};

const appStack = new FastapiDynamodbLambdaStack(app, 'FastapiDynamodbLambdaStack', { env });

// Deploys the stack above on every push to master. Deploy this one by hand
// (`npx cdk deploy FastapiDynamodbLambdaPipeline`); it doesn't update itself.
new ApiPipelineStack(app, 'FastapiDynamodbLambdaPipeline', {
  env,
  appStackName: appStack.stackName,
});
