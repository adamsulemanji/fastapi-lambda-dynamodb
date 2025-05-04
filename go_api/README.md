# Go API Lambda - Meals Service

This directory contains a Go-based API for managing meals data, running as an AWS Lambda function. The code follows a Rails-inspired MVC architecture.

## Project Structure

```
go_api/
├── config/        # Configuration management
├── controllers/   # Business logic for API endpoints
├── models/        # Data models and database operations
├── routes/        # Routing and request handling
├── utils/         # Utility functions and helpers
├── main.go        # Application entry point
├── go.mod         # Go module definition
└── Dockerfile     # Docker container configuration
```

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
2. Set up Go modules:

```bash
# Install dependencies
go mod tidy
```

3. Run the application locally (requires AWS credentials):

```bash
# Set environment variables
export TABLE_NAME=MyTable-dev
export AWS_REGION=us-east-1
export STAGE=dev

# Run
go run main.go
```

## Docker

To build and run the Docker container locally:

```bash
docker build -t go-meals-api .
docker run -p 9000:8080 \
  -e TABLE_NAME=MyTable-dev \
  -e AWS_REGION=us-east-1 \
  -e STAGE=dev \
  -e AWS_ACCESS_KEY_ID=your-key \
  -e AWS_SECRET_ACCESS_KEY=your-secret \
  go-meals-api
```

You can then test the API with:

```bash
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