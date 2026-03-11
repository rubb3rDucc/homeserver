package main

import (
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
)

type QBClient struct {
	baseURL  string
	username string
	password string
	client   *http.Client
}

func NewQBClient(baseURL, username, password string) *QBClient {
	jar, _ := cookiejar.New(nil)
	return &QBClient{
		baseURL:  strings.TrimRight(baseURL, "/"),
		username: username,
		password: password,
		client:   &http.Client{Jar: jar},
	}
}

func (q *QBClient) login() error {
	if q.username == "" && q.password == "" {
		return nil // no auth configured
	}

	data := url.Values{
		"username": {q.username},
		"password": {q.password},
	}
	resp, err := q.client.PostForm(q.baseURL+"/api/v2/auth/login", data)
	if err != nil {
		return fmt.Errorf("qb login request: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK || strings.TrimSpace(string(body)) != "Ok." {
		return fmt.Errorf("qb login failed: %s (status %d)", string(body), resp.StatusCode)
	}
	return nil
}

func (q *QBClient) postAPI(endpoints []string, action string) error {
	if err := q.login(); err != nil {
		return err
	}

	data := url.Values{"hashes": {"all"}}
	for _, endpoint := range endpoints {
		resp, err := q.client.PostForm(q.baseURL+endpoint, data)
		if err != nil {
			return fmt.Errorf("qb %s: %w", action, err)
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			return nil
		}
	}
	return fmt.Errorf("qb %s failed: all endpoints returned non-200", action)
}

func (q *QBClient) PauseAll() error {
	// v5.0+ uses "stop", older versions use "pause"
	return q.postAPI([]string{
		"/api/v2/torrents/stop",
		"/api/v2/torrents/pause",
	}, "pause")
}

func (q *QBClient) ResumeAll() error {
	// v5.0+ uses "start", older versions use "resume"
	return q.postAPI([]string{
		"/api/v2/torrents/start",
		"/api/v2/torrents/resume",
	}, "resume")
}
