#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { FastapiDynamodbLambdaStack } from '../lib/fastapi-dynamodb-lambda-stack';



const app = new cdk.App();

new FastapiDynamodbLambdaStack(app, 'FastapiDynamodbLambdaStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});