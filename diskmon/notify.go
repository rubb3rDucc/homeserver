package main

import (
	"log"

	"github.com/containrrr/shoutrrr"
)

func sendNotification(shoutrrrURL, message string) {
	if shoutrrrURL == "" {
		log.Println("No notification URL configured, skipping notification")
		return
	}

	if err := shoutrrr.Send(shoutrrrURL, message); err != nil {
		log.Printf("Failed to send notification: %v", err)
	}
}
