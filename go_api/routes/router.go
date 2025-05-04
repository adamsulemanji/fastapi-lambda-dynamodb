package routes

import (
	"context"
	"net/http"
	"errors"
	"github.com/aws/aws-lambda-go/events"
	"github.com/awslabs/aws-lambda-go-api-proxy/core"
	"github.com/awslabs/aws-lambda-go-api-proxy/gorillamux"
	"github.com/gorilla/mux"

	"go_api/controllers"
)

// Router directs API requests to the appropriate handler
type Router struct {
	adapter *gorillamux.GorillaMuxAdapter
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
	r.HandleFunc("/", mealsController.HelloHandler).Methods(http.MethodGet)

	// Set up Not Found handler
	r.NotFoundHandler = http.HandlerFunc(notFoundHandler)

	// Create the adapter
	adapter := gorillamux.New(r)

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

// Route handles incoming API Gateway v1 proxy requests
func (r *Router) Route(ctx context.Context, req events.APIGatewayProxyRequest) (events.APIGatewayProxyResponse, error) {
    // 1) Wrap the incoming APIGatewayProxyRequest as a v1 switchable request
    switchReq := core.NewSwitchableAPIGatewayRequestV1(&req)

    // 2) Proxy through GorillaMuxAdapter
    resp, err := r.adapter.ProxyWithContext(ctx, *switchReq)
    if err != nil {
        return events.APIGatewayProxyResponse{StatusCode: http.StatusInternalServerError}, err
    }

    // 3) Extract the APIGatewayProxyResponse
    v1Resp := resp.Version1()
    if v1Resp == nil {
        return events.APIGatewayProxyResponse{StatusCode: http.StatusInternalServerError}, errors.New("missing v1 gateway response")
    }

    return *v1Resp, nil
}
