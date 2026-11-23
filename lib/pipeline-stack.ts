import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as codepipeline from "aws-cdk-lib/aws-codepipeline";
import * as codepipeline_actions from "aws-cdk-lib/aws-codepipeline-actions";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as iam from "aws-cdk-lib/aws-iam";

export interface ApiPipelineStackProps extends cdk.StackProps {
  /** Name of the application stack the pipeline deploys. */
  appStackName: string;
  githubOwner?: string;
  githubRepo?: string;
  branch?: string;
}

/**
 * Push to master -> CodeBuild runs the tests, then `cdk deploy` of the API stack.
 *
 * A CloudFormation deploy action (as the website pipeline uses) can't be used
 * here: the Lambdas are Docker image assets that must be built and pushed to
 * ECR first, which only the CDK CLI does. So one privileged CodeBuild project
 * runs `cdk deploy`, using the CDK bootstrap roles for publishing/deploying.
 *
 * Not self-mutating: changes to this file need a manual
 * `npx cdk deploy FastapiDynamodbLambdaPipeline`.
 */
export class ApiPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ApiPipelineStackProps) {
    super(scope, id, props);

    const sourceOutput = new codepipeline.Artifact("SourceOutput");

    const sourceAction = new codepipeline_actions.GitHubSourceAction({
      actionName: "GitHub_Source",
      owner: props.githubOwner ?? "adamsulemanji",
      repo: props.githubRepo ?? "fastapi-lambda-dynamodb",
      branch: props.branch ?? "master",
      oauthToken: cdk.SecretValue.secretsManager("github_token2"),
      output: sourceOutput,
      trigger: codepipeline_actions.GitHubTrigger.WEBHOOK,
    });

    const deployProject = new codebuild.PipelineProject(this, "DeployProject", {
      description: `Tests and cdk-deploys ${props.appStackName}`,
      environment: {
        // x86 so the linux/amd64 Lambda images build natively (no emulation).
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.MEDIUM,
        privileged: true, // Docker builds for the image assets
      },
      timeout: cdk.Duration.minutes(60),
      cache: codebuild.Cache.local(
        codebuild.LocalCacheMode.DOCKER_LAYER,
        codebuild.LocalCacheMode.CUSTOM,
      ),
      buildSpec: codebuild.BuildSpec.fromObjectToYaml({
        version: "0.2",
        env: {
          variables: { CDK_DISABLE_CLI_TELEMETRY: "true" },
        },
        phases: {
          install: {
            "runtime-versions": { nodejs: "20" },
            commands: ["npm ci"],
          },
          pre_build: {
            commands: ["npx --no-install cdk --version", "npm test -- --runInBand"],
          },
          build: {
            commands: [
              `npx --no-install cdk deploy ${props.appStackName} --require-approval never --no-notices --progress events`,
            ],
          },
        },
        cache: {
          paths: ["/root/.npm/**/*"],
        },
      }),
    });

    // `cdk deploy` does its work by assuming the bootstrap roles
    // (deploy, file-publishing, image-publishing, lookup); that's all it needs.
    deployProject.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [`arn:aws:iam::${this.account}:role/cdk-*`],
      }),
    );

    const deployAction = new codepipeline_actions.CodeBuildAction({
      actionName: "Test_And_Deploy",
      project: deployProject,
      input: sourceOutput,
    });

    new codepipeline.Pipeline(this, "ApiPipeline", {
      pipelineName: "FastapiDynamodbLambdaPipeline",
      pipelineType: codepipeline.PipelineType.V2,
      stages: [
        { stageName: "Source", actions: [sourceAction] },
        { stageName: "Deploy", actions: [deployAction] },
      ],
    });
  }
}
