package main

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"strings"
	"time"
	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/session"
	"github.com/aws/aws-sdk-go/service/dynamodb"
	"github.com/aws/aws-sdk-go/service/dynamodb/dynamodbattribute"
	"github.com/google/uuid"
)

// TableName retrieves table name from environment variable
var (
	tableName string
	dynaClient *dynamodb.DynamoDB
)

func init() {
	tableName = os.Getenv("TABLE_NAME")
	if tableName == "" {
		tableName = "MyTable-prod" // Default to production table
	}
	
	// Initialize DynamoDB client
	sess := session.Must(session.NewSession())
	dynaClient = dynamodb.New(sess)
}

// MealItem represents a meal record
type MealItem struct {
	MealID   string    `json:"mealID"`
	MealName string    `json:"mealName"`
	MealType string    `json:"mealType"`
	EatingOut bool      `json:"eatingOut"`
	Date     string    `json:"date"`
	Note     string    `json:"note"`
}

// ErrorResponse represents an error response
type ErrorResponse struct {
	Message string `json:"message"`
	Error   string `json:"error,omitempty"`
}

// SuccessResponse represents a success response
type SuccessResponse struct {
	Success bool     `json:"success"`
	Item    *MealItem `json:"item,omitempty"`
	Items   []MealItem `json:"items,omitempty"`
	Message string    `json:"message,omitempty"`
}

func handler(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	log.Printf("Processing request: %s %s", req.HTTPMethod, req.Path)

	switch {
	case req.HTTPMethod == "GET" && req.Path == "/meals":
		return getAllMeals(ctx)
	case req.HTTPMethod == "GET" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return getMeal(ctx, mealID)
	case req.HTTPMethod == "POST" && req.Path == "/meals":
		return createMeal(ctx, req)
	case req.HTTPMethod == "PUT" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return updateMeal(ctx, mealID, req)
	case req.HTTPMethod == "DELETE" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return deleteMeal(ctx, mealID)
	case req.HTTPMethod == "DELETE" && req.Path == "/meals":
		return deleteAllMeals(ctx)
	default:
		return respondJSON(404, ErrorResponse{Message: "Not found"})
	}
}

func getAllMeals(ctx context.Context) (events.APIGatewayProxyResponse, error) {
	input := &dynamodb.ScanInput{
		TableName: aws.String(tableName),
	}

	result, err := dynaClient.ScanWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to scan table: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to retrieve meals", Error: err.Error()})
	}

	var meals []MealItem
	err = dynamodbattribute.UnmarshalListOfMaps(result.Items, &meals)
	if err != nil {
		log.Printf("Failed to unmarshal: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to parse meals", Error: err.Error()})
	}

	return respondJSON(200, SuccessResponse{Success: true, Items: meals})
}

func getMeal(ctx context.Context, mealID string) (events.APIGatewayProxyResponse, error) {
	input := &dynamodb.GetItemInput{
		TableName: aws.String(tableName),
		Key: map[string]*dynamodb.AttributeValue{
			"mealID": {
				S: aws.String(mealID),
			},
		},
	}

	result, err := dynaClient.GetItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to get item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to retrieve meal", Error: err.Error()})
	}

	if result.Item == nil {
		return respondJSON(404, ErrorResponse{Message: "Meal not found"})
	}

	var meal MealItem
	err = dynamodbattribute.UnmarshalMap(result.Item, &meal)
	if err != nil {
		log.Printf("Failed to unmarshal: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to parse meal", Error: err.Error()})
	}

	return respondJSON(200, SuccessResponse{Success: true, Item: &meal})
}

func createMeal(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	var meal MealItem
	err := json.Unmarshal([]byte(req.Body), &meal)
	if err != nil {
		log.Printf("Failed to parse request body: %v", err)
		return respondJSON(400, ErrorResponse{Message: "Invalid request body", Error: err.Error()})
	}

	// Validate required fields
	if meal.MealName == "" || meal.MealType == "" {
		return respondJSON(400, ErrorResponse{Message: "MealName and MealType are required"})
	}

	// Generate UUID if not provided
	if meal.MealID == "" {
		meal.MealID = uuid.New().String()
	}

	// Set date to current time if not provided
	if meal.Date == "" {
		meal.Date = time.Now().Format(time.RFC3339)
	}

	// Marshal item to DynamoDB attribute
	item, err := dynamodbattribute.MarshalMap(meal)
	if err != nil {
		log.Printf("Failed to marshal item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to process meal data", Error: err.Error()})
	}

	// Put item in DynamoDB
	input := &dynamodb.PutItemInput{
		TableName: aws.String(tableName),
		Item:      item,
	}

	_, err = dynaClient.PutItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to put item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to save meal", Error: err.Error()})
	}

	return respondJSON(201, SuccessResponse{Success: true, Item: &meal})
}

func updateMeal(ctx context.Context, mealID string, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	// First check if the item exists
	getInput := &dynamodb.GetItemInput{
		TableName: aws.String(tableName),
		Key: map[string]*dynamodb.AttributeValue{
			"mealID": {
				S: aws.String(mealID),
			},
		},
	}

	result, err := dynaClient.GetItemWithContext(ctx, getInput)
	if err != nil {
		log.Printf("Failed to get item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to retrieve meal", Error: err.Error()})
	}

	if result.Item == nil {
		return respondJSON(404, ErrorResponse{Message: "Meal not found"})
	}

	var meal MealItem
	err = json.Unmarshal([]byte(req.Body), &meal)
	if err != nil {
		log.Printf("Failed to parse request body: %v", err)
		return respondJSON(400, ErrorResponse{Message: "Invalid request body", Error: err.Error()})
	}

	// Set the mealID to ensure consistency
	meal.MealID = mealID

	// Marshal item to DynamoDB attribute
	item, err := dynamodbattribute.MarshalMap(meal)
	if err != nil {
		log.Printf("Failed to marshal item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to process meal data", Error: err.Error()})
	}

	// Update item in DynamoDB
	updateInput := &dynamodb.PutItemInput{
		TableName: aws.String(tableName),
		Item:      item,
	}

	_, err = dynaClient.PutItemWithContext(ctx, updateInput)
	if err != nil {
		log.Printf("Failed to update item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to update meal", Error: err.Error()})
	}

	return respondJSON(200, SuccessResponse{Success: true, Item: &meal})
}

func deleteMeal(ctx context.Context, mealID string) (events.APIGatewayProxyResponse, error) {
	input := &dynamodb.DeleteItemInput{
		TableName: aws.String(tableName),
		Key: map[string]*dynamodb.AttributeValue{
			"mealID": {
				S: aws.String(mealID),
			},
		},
	}

	_, err := dynaClient.DeleteItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to delete item: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to delete meal", Error: err.Error()})
	}

	return respondJSON(200, SuccessResponse{Success: true, Message: "Meal deleted"})
}

func deleteAllMeals(ctx context.Context) (events.APIGatewayProxyResponse, error) {
	// Scan to get all items
	scanInput := &dynamodb.ScanInput{
		TableName: aws.String(tableName),
	}

	result, err := dynaClient.ScanWithContext(ctx, scanInput)
	if err != nil {
		log.Printf("Failed to scan table: %v", err)
		return respondJSON(500, ErrorResponse{Message: "Failed to retrieve meals", Error: err.Error()})
	}

	if len(result.Items) == 0 {
		return respondJSON(200, SuccessResponse{Success: true, Message: "No meals to delete"})
	}

	// Delete each item
	for _, item := range result.Items {
		mealID := *item["mealID"].S
		deleteInput := &dynamodb.DeleteItemInput{
			TableName: aws.String(tableName),
			Key: map[string]*dynamodb.AttributeValue{
				"mealID": {
					S: aws.String(mealID),
				},
			},
		}

		_, err := dynaClient.DeleteItemWithContext(ctx, deleteInput)
		if err != nil {
			log.Printf("Failed to delete item %s: %v", mealID, err)
			// Continue deletion despite errors
		}
	}

	return respondJSON(200, SuccessResponse{Success: true, Message: "All meals deleted"})
}

func respondJSON(statusCode int, data interface{}) (events.APIGatewayProxyResponse, error) {
	body, err := json.Marshal(data)
	if err != nil {
		log.Printf("Failed to marshal response: %v", err)
		return events.APIGatewayProxyResponse{
			StatusCode: 500,
			Body:       `{"message":"Internal server error"}`,
		}, nil
	}

	return events.APIGatewayProxyResponse{
		StatusCode: statusCode,
		Headers: map[string]string{
			"Content-Type":                "application/json",
			"Access-Control-Allow-Origin": "*",
		},
		Body: string(body),
	}, nil
}

func main() {
	lambda.Start(handler)
}