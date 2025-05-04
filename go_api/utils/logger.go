package utils

import (
	"log"
	"os"
)

// Logger provides structured logging
type Logger struct {
	*log.Logger
	env string
}

// NewLogger creates a new logger instance
func NewLogger() *Logger {
	env := os.Getenv("STAGE")
	if env == "" {
		env = "dev"
	}

	return &Logger{
		Logger: log.New(os.Stdout, "[GO-API] ", log.LstdFlags),
		env:    env,
	}
}

// Info logs informational messages
func (l *Logger) Info(msg string) {
	l.Printf("[INFO] %s", msg)
}

// Error logs error messages
func (l *Logger) Error(msg string, err error) {
	l.Printf("[ERROR] %s: %v", msg, err)
}

// Debug logs debug messages (only in dev environment)
func (l *Logger) Debug(msg string) {
	if l.env == "dev" {
		l.Printf("[DEBUG] %s", msg)
	}
}

// Single logger instance
var Log *Logger

// Initialize sets up the global logger
func Initialize() {
	Log = NewLogger()
} 