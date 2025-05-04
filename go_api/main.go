package main

import (
	"context"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"

	"go_api/config"
	"go_api/routes"
	"go_api/utils"
)

func init() {
	// Initialize configuration
	config.Initialize()
	
	// Initialize logger
	utils.Initialize()
	
	utils.Log.Info("Application initialized")
}

// Handler processes API Gateway events
func handler(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	// Create router and process the request using gorilla/mux
	router := routes.NewRouter()
	return router.Route(ctx, req)
}

func main() {
	utils.Log.Info("Starting Go Meals API with gorilla/mux")
	lambda.Start(handler)
}