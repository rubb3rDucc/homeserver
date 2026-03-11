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

func (q *QBClient) PauseAll() error {
	if err := q.login(); err != nil {
		return err
	}

	data := url.Values{"hashes": {"all"}}
	resp, err := q.client.PostForm(q.baseURL+"/api/v2/torrents/pause", data)
	if err != nil {
		return fmt.Errorf("qb pause: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("qb pause failed: status %d", resp.StatusCode)
	}
	return nil
}

func (q *QBClient) ResumeAll() error {
	if err := q.login(); err != nil {
		return err
	}

	data := url.Values{"hashes": {"all"}}
	resp, err := q.client.PostForm(q.baseURL+"/api/v2/torrents/resume", data)
	if err != nil {
		return fmt.Errorf("qb resume: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("qb resume failed: status %d", resp.StatusCode)
	}
	return nil
}
