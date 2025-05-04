# Go API Lambda - Meals Service

This directory contains a Go-based API for managing meals data, running as an AWS Lambda function.

## Endpoints

The API provides the following endpoints:

- `GET /meals` - List all meals
- `GET /meals/{id}` - Get a specific meal by ID
- `POST /meals` - Create a new meal
- `PUT /meals/{id}` - Update an existing meal
- `DELETE /meals/{id}` - Delete a specific meal
- `DELETE /meals` - Delete all meals

## Data Model

The meal data model includes:

```json
{
  "mealID": "string",
  "mealName": "string",
  "mealType": "string",
  "eatingOut": true|false,
  "date": "2023-05-17T20:21:10Z",
  "note": "string"
}
```

## Local Development

To run this API locally for development:

1. Install Go (version 1.18 or later)
2. Install AWS SAM CLI for local Lambda testing
3. Install dependencies:

```
go mod init go-api
go get github.com/aws/aws-lambda-go/events
go get github.com/aws/aws-lambda-go/lambda
go get github.com/aws/aws-sdk-go/aws
go get github.com/aws/aws-sdk-go/aws/session
go get github.com/aws/aws-sdk-go/service/dynamodb
go get github.com/aws/aws-sdk-go/service/dynamodb/dynamodbattribute
go get github.com/google/uuid
```

## Docker

To build and run the Docker container locally:

```
docker build -t go-api .
docker run -p 9000:8080 \
  -e TABLE_NAME=MyTable-dev \
  -e AWS_REGION=us-east-1 \
  go-api
```

You can then test the API with:

```
# List all meals
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{
  "resource": "/meals", 
  "path": "/meals", 
  "httpMethod": "GET", 
  "headers": {"Accept": "*/*"}, 
  "requestContext": {"resourcePath": "/meals", "httpMethod": "GET"}, 
  "isBase64Encoded": false
}'

# Create a meal
curl -X POST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{
  "resource": "/meals", 
  "path": "/meals", 
  "httpMethod": "POST", 
  "headers": {"Content-Type": "application/json"}, 
  "body": "{\"mealName\": \"Chicken Curry\", \"mealType\": \"Dinner\", \"eatingOut\": false, \"date\": \"2023-05-17T20:21:10Z\", \"note\": \"Delicious meal\"}", 
  "requestContext": {"resourcePath": "/meals", "httpMethod": "POST"}, 
  "isBase64Encoded": false
}'
```

## Deployment

The API is deployed as part of the CDK stack. When you deploy the stack, this API will be deployed as a Lambda function with an API Gateway in front of it. 