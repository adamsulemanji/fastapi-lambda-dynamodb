package config

import (
	"os"

	"github.com/aws/aws-sdk-go/aws"
	"github.com/aws/aws-sdk-go/aws/session"
	"github.com/aws/aws-sdk-go/service/dynamodb"
)

// Config holds application configuration
type Config struct {
	TableName string
	AwsRegion string
	DbClient  *dynamodb.DynamoDB
}

// New creates a new Config with values from environment
func New() *Config {
	tableName := os.Getenv("TABLE_NAME")
	if tableName == "" {
		tableName = "MyTable-prod" // Default to production table
	}

	awsRegion := os.Getenv("AWS_REGION")
	if awsRegion == "" {
		awsRegion = "us-east-1" // Default region
	}

	// Initialize AWS session
	sess := session.Must(session.NewSession(&aws.Config{
		Region: aws.String(awsRegion),
	}))

	// Create DynamoDB client
	dbClient := dynamodb.New(sess)

	return &Config{
		TableName: tableName,
		AwsRegion: awsRegion,
		DbClient:  dbClient,
	}
}

// Global instance of Config
var AppConfig *Config

// Initialize sets up the global config
func Initialize() {
	AppConfig = New()
} 