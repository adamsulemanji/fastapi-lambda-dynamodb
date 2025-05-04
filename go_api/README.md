# Go API Lambda - Hello World

This directory contains a simple Go-based Hello World API that runs as an AWS Lambda function.

## Endpoint

The API has a single endpoint that returns a Hello World message:

```json
{
  "message": "Hello, World!"
}
```

This will be returned for any path you call on the API.

## Local Development

To run this API locally for development:

1. Install Go (version 1.18 or later)
2. Install AWS SAM CLI for local Lambda testing

```
# Initialize Go modules
go mod init go-api
go mod tidy

# Build the application
go build -o main .

# Test locally
./main
```

## Docker

To build and run the Docker container locally:

```
docker build -t go-api .
docker run -p 9000:8080 go-api
```

You can then test the API with:

```
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{"resource": "/", "path": "/", "httpMethod": "GET", "headers": {"Accept": "*/*"}, "multiValueHeaders": {"Accept": ["*/*"]}, "queryStringParameters": null, "multiValueQueryStringParameters": null, "pathParameters": null, "stageVariables": null, "requestContext": {"resourceId": "123456", "resourcePath": "/", "httpMethod": "GET"}, "body": null, "isBase64Encoded": false}'
```

## Deployment

The API is deployed as part of the CDK stack. When you deploy the stack, this API will be deployed as a Lambda function with an API Gateway in front of it. 