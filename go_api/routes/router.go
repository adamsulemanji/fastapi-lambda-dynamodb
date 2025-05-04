package routes

import (
	"context"
	"net/http"

	"github.com/aws/aws-lambda-go/events"
	"github.com/awslabs/aws-lambda-go-api-proxy/mux"
	"github.com/gorilla/mux"

	"go-api/controllers"
)

// Router directs API requests to the appropriate handler
type Router struct {
	adapter *muxadapter.GorillaMuxAdapter
}

// NewRouter creates a new router instance with gorilla/mux
func NewRouter() *Router {
	r := mux.NewRouter()
	mealsController := &controllers.MealsController{}

	// Set up CORS handling
	r.Use(corsMiddleware)

	// Set up routes
	r.HandleFunc("/meals", mealsController.IndexHandler).Methods(http.MethodGet)
	r.HandleFunc("/meals/{id}", mealsController.ShowHandler).Methods(http.MethodGet)
	r.HandleFunc("/meals", mealsController.CreateHandler).Methods(http.MethodPost)
	r.HandleFunc("/meals/{id}", mealsController.UpdateHandler).Methods(http.MethodPut)
	r.HandleFunc("/meals/{id}", mealsController.DestroyHandler).Methods(http.MethodDelete)
	r.HandleFunc("/meals", mealsController.DestroyAllHandler).Methods(http.MethodDelete)
	
	// Set up Not Found handler
	r.NotFoundHandler = http.HandlerFunc(notFoundHandler)
	
	// Create the adapter
	adapter := muxadapter.New(r)

	return &Router{
		adapter: adapter,
	}
}

// corsMiddleware handles CORS headers
func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusOK)
			return
		}
		
		next.ServeHTTP(w, r)
	})
}

// notFoundHandler handles 404 responses
func notFoundHandler(w http.ResponseWriter, r *http.Request) {
	response, _ := controllers.NotFound()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(response.StatusCode)
	w.Write([]byte(response.Body))
}

// Route handles incoming API Gateway requests
func (r *Router) Route(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
	return r.adapter.ProxyWithContext(ctx, req)
} 