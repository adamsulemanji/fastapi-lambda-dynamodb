package routes

import (
	"context"
	"log"
	"strings"

	"github.com/aws/aws-lambda-go/events"

	"go-api/controllers"
)

// Router directs API requests to the appropriate handler
type Router struct {
	mealsController *controllers.MealsController
}

// NewRouter creates a new router instance
func NewRouter() *Router {
	return &Router{
		mealsController: &controllers.MealsController{},
	}
}

// Route handles incoming API Gateway requests
func (r *Router) Route(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	log.Printf("Processing request: %s %s", req.HTTPMethod, req.Path)

	// Handle CORS preflight requests
	if req.HTTPMethod == "OPTIONS" {
		return events.APIGatewayProxyResponse{
			StatusCode: 200,
			Headers: map[string]string{
				"Access-Control-Allow-Origin":  "*",
				"Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
				"Access-Control-Allow-Headers": "Content-Type,Authorization",
			},
			Body: "",
		}, nil
	}

	// Route requests to appropriate controller actions
	switch {
	// GET /meals - List all meals
	case req.HTTPMethod == "GET" && req.Path == "/meals":
		return r.mealsController.Index(ctx)

	// GET /meals/:id - Show a specific meal
	case req.HTTPMethod == "GET" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return r.mealsController.Show(ctx, mealID)

	// POST /meals - Create a new meal
	case req.HTTPMethod == "POST" && req.Path == "/meals":
		return r.mealsController.Create(ctx, req.Body)

	// PUT /meals/:id - Update a meal
	case req.HTTPMethod == "PUT" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return r.mealsController.Update(ctx, mealID, req.Body)

	// DELETE /meals/:id - Delete a meal
	case req.HTTPMethod == "DELETE" && strings.HasPrefix(req.Path, "/meals/"):
		mealID := strings.TrimPrefix(req.Path, "/meals/")
		return r.mealsController.Destroy(ctx, mealID)

	// DELETE /meals - Delete all meals
	case req.HTTPMethod == "DELETE" && req.Path == "/meals":
		return r.mealsController.DestroyAll(ctx)

	// Handle unknown routes
	default:
		return controllers.NotFound()
	}
} 