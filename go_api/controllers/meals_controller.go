package controllers

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"

	"github.com/aws/aws-lambda-go/events"
	"github.com/gorilla/mux"

	"go_api/models"
)

// Response types
type ErrorResponse struct {
	Message string `json:"message"`
	Error   string `json:"error,omitempty"`
}

type SuccessResponse struct {
	Success bool             `json:"success"`
	Item    *models.MealItem `json:"item,omitempty"`
	Items   []models.MealItem `json:"items,omitempty"`
	Message string           `json:"message,omitempty"`
}

// MealsController handles meal-related logic
type MealsController struct{}


// HelloHandler handles the hello route
func (c *MealsController) HelloHandler(w http.ResponseWriter, r *http.Request) {
	resp, _ := respondJSON(200, SuccessResponse{
		Success: true,
		Message: "Hello, world!",
	})
	writeResponse(w, resp)
}

// IndexHandler lists all meals
func (c *MealsController) IndexHandler(w http.ResponseWriter, r *http.Request) {
	resp, _ := c.Index(r.Context())
	writeResponse(w, resp)
}

// ShowHandler gets a specific meal
func (c *MealsController) ShowHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	resp, _ := c.Show(r.Context(), vars["id"])
	writeResponse(w, resp)
}

// CreateHandler adds a new meal
func (c *MealsController) CreateHandler(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		resp, _ := respondJSON(400, ErrorResponse{
			Message: "Failed to read request body",
			Error:   err.Error(),
		})
		writeResponse(w, resp)
		return
	}
	resp, _ := c.Create(r.Context(), string(body))
	writeResponse(w, resp)
}

// UpdateHandler modifies an existing meal
func (c *MealsController) UpdateHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		resp, _ := respondJSON(400, ErrorResponse{
			Message: "Failed to read request body",
			Error:   err.Error(),
		})
		writeResponse(w, resp)
		return
	}
	resp, _ := c.Update(r.Context(), vars["id"], string(body))
	writeResponse(w, resp)
}

// DestroyHandler removes a meal
func (c *MealsController) DestroyHandler(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	resp, _ := c.Destroy(r.Context(), vars["id"])
	writeResponse(w, resp)
}

// DestroyAllHandler removes all meals
func (c *MealsController) DestroyAllHandler(w http.ResponseWriter, r *http.Request) {
	resp, _ := c.DestroyAll(r.Context())
	writeResponse(w, resp)
}

// Helper to write API Gateway response to HTTP response
func writeResponse(w http.ResponseWriter, resp events.APIGatewayProxyResponse) {
	for k, v := range resp.Headers {
		w.Header().Set(k, v)
	}
	w.WriteHeader(resp.StatusCode)
	w.Write([]byte(resp.Body))
}

// Index lists all meals
func (c *MealsController) Index(ctx context.Context) (events.APIGatewayProxyResponse, error) {
	meals, err := models.FindAll(ctx)
	if err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to retrieve meals",
			Error:   err.Error(),
		})
	}

	return respondJSON(200, SuccessResponse{
		Success: true,
		Items:   meals,
	})
}

// Show gets a specific meal
func (c *MealsController) Show(ctx context.Context, mealID string) (events.APIGatewayProxyResponse, error) {
	meal, err := models.FindByID(ctx, mealID)
	if err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to retrieve meal",
			Error:   err.Error(),
		})
	}

	if meal == nil {
		return respondJSON(404, ErrorResponse{
			Message: "Meal not found",
		})
	}

	return respondJSON(200, SuccessResponse{
		Success: true,
		Item:    meal,
	})
}

// Create adds a new meal
func (c *MealsController) Create(ctx context.Context, body string) (events.APIGatewayProxyResponse, error) {
	var meal models.MealItem
	if err := json.Unmarshal([]byte(body), &meal); err != nil {
		return respondJSON(400, ErrorResponse{
			Message: "Invalid request body",
			Error:   err.Error(),
		})
	}

	if !meal.Validate() {
		return respondJSON(400, ErrorResponse{
			Message: "MealName and MealType are required",
		})
	}

	if err := meal.Create(ctx); err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to save meal",
			Error:   err.Error(),
		})
	}

	return respondJSON(201, SuccessResponse{
		Success: true,
		Item:    &meal,
	})
}

// Update modifies an existing meal
func (c *MealsController) Update(ctx context.Context, mealID string, body string) (events.APIGatewayProxyResponse, error) {
	// First check if the item exists
	existingMeal, err := models.FindByID(ctx, mealID)
	if err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to retrieve meal",
			Error:   err.Error(),
		})
	}

	if existingMeal == nil {
		return respondJSON(404, ErrorResponse{
			Message: "Meal not found",
		})
	}

	var meal models.MealItem
	if err := json.Unmarshal([]byte(body), &meal); err != nil {
		return respondJSON(400, ErrorResponse{
			Message: "Invalid request body",
			Error:   err.Error(),
		})
	}

	// Set the ID to ensure consistency
	meal.MealID = mealID

	if err := meal.Update(ctx); err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to update meal",
			Error:   err.Error(),
		})
	}

	return respondJSON(200, SuccessResponse{
		Success: true,
		Item:    &meal,
	})
}

// Destroy removes a meal
func (c *MealsController) Destroy(ctx context.Context, mealID string) (events.APIGatewayProxyResponse, error) {
	if err := models.Delete(ctx, mealID); err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to delete meal",
			Error:   err.Error(),
		})
	}

	return respondJSON(200, SuccessResponse{
		Success: true,
		Message: "Meal deleted",
	})
}

// DestroyAll removes all meals
func (c *MealsController) DestroyAll(ctx context.Context) (events.APIGatewayProxyResponse, error) {
	if err := models.DeleteAll(ctx); err != nil {
		return respondJSON(500, ErrorResponse{
			Message: "Failed to delete all meals",
			Error:   err.Error(),
		})
	}

	return respondJSON(200, SuccessResponse{
		Success: true,
		Message: "All meals deleted",
	})
}

// NotFound returns a 404 response for non-existent routes
func NotFound() (events.APIGatewayProxyResponse, error) {
	return respondJSON(404, ErrorResponse{
		Message: "Not found",
	})
}

// Helper function to format JSON responses
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