package models

import (
	"context"
	"log"
	"time"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/service/dynamodb"
	"github.com/aws/aws-sdk-go/service/dynamodb/dynamodbattribute"
	"github.com/google/uuid"

	"go_api/config"
)

// MealItem represents a meal record
type MealItem struct {
	MealID    string `json:"mealID"`
	MealName  string `json:"mealName"`
	MealType  string `json:"mealType"`
	EatingOut bool   `json:"eatingOut"`
	Date      string `json:"date"`
	Note      string `json:"note"`
	Tags      []string `json:"tags"`
}

// Validate checks if a meal has required fields
func (m *MealItem) Validate() bool {
	return m.MealName != "" && m.MealType != ""
}

// BeforeSave prepares a meal for saving
func (m *MealItem) BeforeSave() {
	// Generate UUID if not provided
	if m.MealID == "" {
		m.MealID = uuid.New().String()
	}

	// Set date to current time if not provided
	if m.Date == "" {
		m.Date = time.Now().Format(time.RFC3339)
	}
}

// Create saves a new meal to DynamoDB
func (m *MealItem) Create(ctx context.Context) error {
	m.BeforeSave()

	// Marshal item to DynamoDB attribute
	item, err := dynamodbattribute.MarshalMap(m)
	if err != nil {
		log.Printf("Failed to marshal item: %v", err)
		return err
	}

	// Put item in DynamoDB
	input := &dynamodb.PutItemInput{
		TableName: aws.String(config.AppConfig.TableName),
		Item:      item,
	}

	_, err = config.AppConfig.DbClient.PutItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to put item: %v", err)
		return err
	}

	return nil
}

// Update modifies an existing meal
func (m *MealItem) Update(ctx context.Context) error {
	// Marshal item to DynamoDB attribute
	item, err := dynamodbattribute.MarshalMap(m)
	if err != nil {
		log.Printf("Failed to marshal item: %v", err)
		return err
	}

	// Update item in DynamoDB
	input := &dynamodb.PutItemInput{
		TableName: aws.String(config.AppConfig.TableName),
		Item:      item,
	}

	_, err = config.AppConfig.DbClient.PutItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to update item: %v", err)
		return err
	}

	return nil
}

// FindByID gets a meal by ID
func FindByID(ctx context.Context, mealID string) (*MealItem, error) {
	input := &dynamodb.GetItemInput{
		TableName: aws.String(config.AppConfig.TableName),
		Key: map[string]*dynamodb.AttributeValue{
			"mealID": {
				S: aws.String(mealID),
			},
		},
	}

	result, err := config.AppConfig.DbClient.GetItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to get item: %v", err)
		return nil, err
	}

	if result.Item == nil {
		return nil, nil // Not found
	}

	var meal MealItem
	err = dynamodbattribute.UnmarshalMap(result.Item, &meal)
	if err != nil {
		log.Printf("Failed to unmarshal: %v", err)
		return nil, err
	}

	return &meal, nil
}

// FindAll gets all meals
func FindAll(ctx context.Context) ([]MealItem, error) {
	input := &dynamodb.ScanInput{
		TableName: aws.String(config.AppConfig.TableName),
	}

	result, err := config.AppConfig.DbClient.ScanWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to scan table: %v", err)
		return nil, err
	}

	var meals []MealItem
	err = dynamodbattribute.UnmarshalListOfMaps(result.Items, &meals)
	if err != nil {
		log.Printf("Failed to unmarshal: %v", err)
		return nil, err
	}

	return meals, nil
}

// Delete removes a meal by ID
func Delete(ctx context.Context, mealID string) error {
	input := &dynamodb.DeleteItemInput{
		TableName: aws.String(config.AppConfig.TableName),
		Key: map[string]*dynamodb.AttributeValue{
			"mealID": {
				S: aws.String(mealID),
			},
		},
	}

	_, err := config.AppConfig.DbClient.DeleteItemWithContext(ctx, input)
	if err != nil {
		log.Printf("Failed to delete item: %v", err)
		return err
	}

	return nil
}

// DeleteAll removes all meals
func DeleteAll(ctx context.Context) error {
	// Get all items
	meals, err := FindAll(ctx)
	if err != nil {
		return err
	}

	// Delete each item
	for _, meal := range meals {
		err := Delete(ctx, meal.MealID)
		if err != nil {
			log.Printf("Failed to delete item %s: %v", meal.MealID, err)
			// Continue deletion despite errors
		}
	}

	return nil
} 