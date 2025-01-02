import { serve } from "bun";

const server = serve({
    port: 3000,
    hostname: "0.0.0.0",
    async fetch(req) {
        const url = new URL(req.url);
        
        // Basic health check endpoint
        if (url.pathname === "/health") {
            return new Response("OK!!", { status: 200 });
        }

        // Example API endpoint
        if (url.pathname === "/api/hello") {
            return new Response(JSON.stringify({
                message: "Hello from Bun.js API!",
                timestamp: new Date().toISOString()
            }), {
                headers: {
                    "Content-Type": "application/json"
                }
            });
        }

        return new Response("Not Found", { status: 404 });
    },
});

console.log(`Server running at http://0.0.0.0:${server.port}`);