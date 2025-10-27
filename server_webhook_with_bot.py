{
  "version": 2,
  "builds": [
    { "src": "server_webhook_with_bot.py", "use": "@vercel/python" }
  ],
  "routes": [
    { "src": "/(.*)", "dest": "server_webhook_with_bot.py" }
  ]
}
