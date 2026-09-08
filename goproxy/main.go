// goproxy terminates WebSocket connections for wireview and forwards frames to a
// Python host over one multiplexed Unix socket (see wireview/contrib/gohost.py).
//
// Wire format, one JSON object per line in both directions:
//
//	{"c": "<conn id>", "t": "open", "path": "/__wireview__", "query": "", "headers": [["cookie", "..."]]}
//	{"c": "<conn id>", "t": "frame", "text": "..."}
//	{"c": "<conn id>", "t": "close", "code": 1000}
//
// The Python side answers with "accept", "frame" and "close" for the same id.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"
)

type message struct {
	C       string      `json:"c"`
	T       string      `json:"t"`
	Path    string      `json:"path,omitempty"`
	Query   string      `json:"query,omitempty"`
	Headers [][2]string `json:"headers,omitempty"`
	Text    string      `json:"text,omitempty"`
	Code    int         `json:"code,omitempty"`
}

type session struct {
	conn   *websocket.Conn
	cancel context.CancelFunc
}

type proxy struct {
	out      chan message
	sessions sync.Map // id -> *session
	counter  uint64
}

func (p *proxy) register(s *session) string {
	id := strconv.FormatUint(atomic.AddUint64(&p.counter, 1), 36)
	p.sessions.Store(id, s)
	return id
}

// handle serves one browser WebSocket: announce it to Python, then pump frames.
func (p *proxy) handle(w http.ResponseWriter, r *http.Request) {
	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns:  []string{"*"},
		CompressionMode: websocket.CompressionDisabled,
	})
	if err != nil {
		return
	}
	ctx, cancel := context.WithCancel(r.Context())
	s := &session{conn: c, cancel: cancel}
	id := p.register(s)

	headers := make([][2]string, 0, len(r.Header))
	for k, vs := range r.Header {
		for _, v := range vs {
			headers = append(headers, [2]string{strings.ToLower(k), v})
		}
	}
	p.out <- message{C: id, T: "open", Path: r.URL.Path, Query: r.URL.RawQuery, Headers: headers}

	defer func() {
		p.sessions.Delete(id)
		cancel()
		c.CloseNow()
	}()
	for {
		typ, data, err := c.Read(ctx)
		if err != nil {
			code := int(websocket.CloseStatus(err))
			if code < 0 {
				code = 1006
			}
			p.out <- message{C: id, T: "close", Code: code}
			return
		}
		if typ != websocket.MessageText {
			continue // wireview speaks JSON text only
		}
		p.out <- message{C: id, T: "frame", Text: string(data)}
	}
}

// writer serializes everything going to Python onto the single backend socket.
func (p *proxy) writer(backend net.Conn) {
	enc := json.NewEncoder(backend)
	for m := range p.out {
		if err := enc.Encode(m); err != nil {
			log.Fatalf("backend write failed: %v", err)
		}
	}
}

// reader dispatches Python's replies to the right browser connection.
func (p *proxy) reader(backend net.Conn) {
	scanner := bufio.NewScanner(backend)
	scanner.Buffer(make([]byte, 1<<20), 64<<20)
	for scanner.Scan() {
		var m message
		if err := json.Unmarshal(scanner.Bytes(), &m); err != nil {
			log.Printf("bad line from backend: %v", err)
			continue
		}
		v, ok := p.sessions.Load(m.C)
		if !ok {
			continue // already gone
		}
		s := v.(*session)
		switch m.T {
		case "frame":
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			err := s.conn.Write(ctx, websocket.MessageText, []byte(m.Text))
			cancel()
			if err != nil {
				s.cancel()
			}
		case "close":
			code := websocket.StatusCode(m.Code)
			if m.Code == 0 {
				code = websocket.StatusNormalClosure
			}
			s.conn.Close(code, "")
			s.cancel()
		case "accept":
			// The handshake was completed before Python was asked; nothing to do.
		}
	}
	log.Fatalf("backend closed: %v", scanner.Err())
}

func main() {
	listen := flag.String("listen", "127.0.0.1:8100", "address to accept browser WebSockets on")
	backendPath := flag.String("backend", "/tmp/wireview-gohost.sock", "Unix socket of `manage.py wireview_gohost`")
	flag.Parse()

	backend, err := net.Dial("unix", *backendPath)
	if err != nil {
		log.Fatalf("cannot reach Python host at %s: %v", *backendPath, err)
	}
	p := &proxy{out: make(chan message, 4096)}
	go p.writer(backend)
	go p.reader(backend)

	http.HandleFunc("/", p.handle)
	log.Printf("goproxy listening on %s, backend %s", *listen, *backendPath)
	log.Fatal(http.ListenAndServe(*listen, nil))
}
