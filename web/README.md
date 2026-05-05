# AI Token Usage - Web Application

This is a Flask web application for visualizing AI token usage data.

## Features

- Daily token usage summary
- Trend charts (line charts)
- Tool comparison charts (bar/pie charts)
- REST API endpoints

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Run the web server
python3 web.py

# Or use flask command
flask run
```

The web interface will be available at http://localhost:5000

## API Endpoints

- `GET /api/summary` - Get summary statistics
- `GET /api/today` - Get today's usage
- `GET /api/<tool_name>/<days>` - Get usage for a tool over N days
- `GET /api/date/<date>` - Get usage for a specific date

## Chart.js

The web interface uses Chart.js for data visualization.
