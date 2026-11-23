import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiPipelineStack } from '../lib/pipeline-stack';

// The pipeline runs these before every deploy, so keep them fast: the app
// stack needs Docker asset hashing + a hosted-zone lookup, so it's not
// synthesized here.
describe('ApiPipelineStack', () => {
  const app = new cdk.App();
  const stack = new ApiPipelineStack(app, 'TestPipeline', {
    env: { account: '111111111111', region: 'us-east-1' },
    appStackName: 'FastapiDynamodbLambdaStack',
  });
  const template = Template.fromStack(stack);

  test('sources from GitHub master and has a deploy stage', () => {
    template.hasResourceProperties('AWS::CodePipeline::Pipeline', {
      Stages: [
        Match.objectLike({
          Name: 'Source',
          Actions: [
            Match.objectLike({
              Configuration: Match.objectLike({
                Repo: 'fastapi-lambda-dynamodb',
                Branch: 'master',
              }),
            }),
          ],
        }),
        Match.objectLike({ Name: 'Deploy' }),
      ],
    });
  });

  test('build is privileged (Docker) and deploys the app stack', () => {
    template.hasResourceProperties('AWS::CodeBuild::Project', {
      Environment: Match.objectLike({ PrivilegedMode: true }),
      Source: Match.objectLike({
        BuildSpec: Match.stringLikeRegexp(
          'cdk deploy FastapiDynamodbLambdaStack --require-approval never',
        ),
      }),
    });
  });

  test('build role can only assume the CDK bootstrap roles', () => {
    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'sts:AssumeRole',
            Resource: 'arn:aws:iam::111111111111:role/cdk-*',
          }),
        ]),
      },
    });
  });
});
