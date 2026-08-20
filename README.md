# SMART STOCK & PROCUREMENT MCP
Smart Stock & Procurement MCP is an AI-powered Model Context Protocol (MCP)-based smart inventory management and supply chain automation system.

## Core Objective : 
The primary purpose of the system is to track inventory in the warehouse, identify products that have fallen below critical levels, and optimize and improve the purchasing process for these products based on the most appropriate criteria in external marketplaces. Users can manage the process using natural-language commands.

## Features
- Natural Language Querying : Ability to query warehouse status and command the system using natural language instructions. 
- Smart Stock Replenishment : Dynamic calculation of stock needs based on target stock levels, current inventory and pending incoming orders. 
- Automated Stock Tracking : Real-time tracking of warehouse inventory, automatically detecting out of stock and low stock products.
- Multi-Criteria Decision Making : Finding the most optimal offer using multiple strategies (cheapest, fastest, highest rated).
- Dynamic Execution Planning : Generating JSON-based multi-step workflows dynamically using LLM and chaining steps with variable passing ($from) and data transformations.

## General Architecture and Components
The system consists of three main layers : 
 - Orchestrator / Client Layer : It receives natural-language queries from the user and, using the Qwen LLM, generates an execution plan. It executes the plan step by step, interprets the technical results obtained through reasoning, and returns the result to the user in natural language. 

 - MCP Server Layer : It provides tools and functions that the LLM can use. It communicates with the orchestrator via the stdio protocol. 
    * Stock MCP Server : It includes tools that perform functions such as checking warehouse inventory, listing critical products, and restocking. 
    * Marketplace MCP Server : It includes tools such as searching for offers on the marketplace, comparing offers, creating a shopping cart, and placing an order.  

 - Backend Service Layer : It is based on Spring Boot. It provides REST APIs that manage all business logic (inventory records, vendor information, quotes, and orders) by communicating directly with the database.

## Technologies Used
     * Orchestrator / Client Layer : Python (mcp, requests), Qwen LLM
     * MCP Server Layer : Python (mcp, httpx, pydantic)
     * Backend Service Layer : Spring Boot(Java 21, Spring Data JPA, Hibernate, Lombok), Maven, PostegreSQL




## Installation

Follow the steps below to set up and run the project locally.

### Prerequisites

Make sure the following software is installed on your machine:

- Java 21
- Maven 3.9+
- Python 3.14+
- PostgreSQL 17
- Git

### 1. Clone the Repository

```bash
git clone https://github.com/beyzzzaaa/smart-stock-mcp.git
cd smart-stock-mcp
```

### 2. Configure PostgreSQL

1. Create a PostgreSQL database named `smart_stock`.
2. Open **[application.yml](stock-service/src/main/resources/application.yml)** in the `stock-service/src/main/resources` folder.
3. Update the database credentials to match your local PostgreSQL configuration:

```yaml
spring:
  datasource:
    url: jdbc:postgresql://localhost:5432/smart_stock
    username: your_postgresql_username
    password: your_postgresql_password
```

The normal profile uses `smart_stock`, listens on port `8081`, and updates the schema without deleting existing inventory or order data. `DB_URL` still overrides the complete JDBC URL, while `SERVER_PORT` overrides the port. Do not set `DB_DDL_AUTO=create` for daily development; its default is the non-destructive `update`. SQL logging is disabled by default and can be enabled with `JPA_SHOW_SQL=true`.

### A. Web development environment

Use four PowerShell terminals so the daily web stack remains on `smart_stock` and ports 8081/11434/8000/5173.

```powershell
# Terminal 1 — PostgreSQL must already expose the smart_stock database
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock"
$env:DB_USERNAME = "postgres"
$env:DB_PASSWORD = "your_password"
$env:SERVER_PORT = "8081"
cd stock-service
mvn spring-boot:run
```

```powershell
# Terminal 2
ollama serve
```

```powershell
# Terminal 3
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8081"
uvicorn web_api:app --host 0.0.0.0 --port 8000
```

```powershell
# Terminal 4
cd web-ui
$env:VITE_API_BASE_URL = "http://localhost:8081"
$env:VITE_LLM_HOST_URL = "http://localhost:8000"
npm run dev
```

### B. Isolated acceptance environment

Create `smart_stock_acceptance` once. Start a second Spring process in a new PowerShell terminal; it can run concurrently with the web backend because both its port and database differ:

```powershell
$env:SPRING_PROFILES_ACTIVE = "acceptance"
$env:SERVER_PORT = "8082"
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock_acceptance"
$env:DB_USERNAME = "postgres"
$env:DB_PASSWORD = "your_password"
$env:PGPASSWORD = $env:DB_PASSWORD
cd stock-service
mvn spring-boot:run
```

The acceptance profile defaults to `smart_stock_acceptance` and recreates only that dedicated schema at service startup. The reset wrapper reads the **same `DB_URL` as Spring**, derives host, port, and database from it, and refuses any database whose name does not end in `_acceptance`. Reset and both seed files run in one error-stopping PostgreSQL transaction. This prevents independently configured reset variables from drifting toward `smart_stock`.

For repeatable write scenarios, the acceptance runner requires a reset command. When supplied, it invokes the command before every selected attempt—including read-only scenarios—so scenario ordering cannot leak state. From `llm-host`, run:

```powershell
cd llm-host
$env:STOCK_SERVICE_URL = "http://localhost:8082"
$env:DB_URL = "jdbc:postgresql://localhost:5432/smart_stock_acceptance"
$env:DB_USERNAME = "postgres"
$env:PGPASSWORD = "your_password"
python acceptance_runner.py `
  --only pending_orders_receive `
  --runs 3 `
  --reset-command 'powershell -NoProfile -File "..\stock-service\scripts\reset-acceptance.ps1"'
```

`--reset-command` is an explicitly trusted local shell command (not remote input); shell execution is retained so quoted Windows paths containing spaces work. A reset is run before every selected attempt when the option is present. A failure reports both stdout and stderr and stops before that attempt reaches the LLM. Write scenarios cannot start without it. Multiple read-only targets can be selected by repeating `--only` and need no reset by default:

```powershell
python acceptance_runner.py --only max_delivery_days --only pending_orders_listing_only --runs 1
```

#### Web smoke checklist

- Open <http://localhost:5173> and verify dashboard KPI data loads.
- Verify the stock table and product filters work.
- Open the marketplace and pending-order pages.
- Verify AI chat communicates with <http://localhost:8000>.
- Send “Bekleyen siparişleri kontrol et ve teslim edilen ürünleri stoğa ekle.” and verify the first turn only lists orders and does not change stock.
- Before approval, verify the trace contains `list_incoming_orders` but not `receive_orders`.
- Send “Onaylıyorum.” and verify only records whose delivery date has arrived are added to stock; the new trace must contain `receive_orders`.
- Exercise delivery/rating/budget commands and verify `max_delivery_days`, `min_rating`, and `max_total_budget` appear with the intended values in trace arguments.

### 3. Build and Run the Spring Boot Backend

Navigate to the `stock-service` directory:

```bash
cd stock-service
```

Build and start the Spring Boot application:

```bash
mvn clean install
mvn spring-boot:run
```

The backend service will be available at:

```text
http://localhost:8081
```

### 4. Install Python Dependencies

Open a new terminal in the project root directory and install the dependencies for all Python components:

```bash
pip install -r llm-host/requirements.txt -r stock-mcp/requirements.txt -r marketplace-mcp/requirements.txt
```

### 5. Configure the LLM

The orchestrator uses Ollama at `http://localhost:11434` and the `qwen3:8b` model by default. Start Ollama and make sure the model is available:

```bash
ollama serve
ollama pull qwen3:8b
```

The endpoint and model can be overridden without editing the code:

```powershell
$env:OLLAMA_URL = "http://localhost:11434/api/generate"
$env:OLLAMA_MODEL = "qwen3:8b"
```

### 6. Start the Orchestrator Client

From the project root directory, navigate to the `llm-host` folder:

```bash
cd llm-host
```

Start the application:

```bash
python app.py
```

Once started, the orchestrator client connects to the Stock MCP Server and Marketplace MCP Server, communicates with the configured LLM, generates execution plans, invokes the required MCP tools, and returns the final response to the user.

## Usage

Once the Spring Boot backend, LLM server, and orchestrator client are running, you can interact with the system using natural language queries in either Turkish or English.

Example queries include:
- *Find the products that need replenishment.*
- *Find the cheapest purchasing plan for products that are low in stock.*
- *Show products that are currently out of stock.*
- *Compare marketplace offers for the products that need replenishment.*

The system interprets the request using the configured LLM, determines the required MCP tools, executes the appropriate operations, and returns the result in natural language.

---

## Available MCP Tools

The system exposes the following tools to the LLM orchestrator through the MCP servers:

### 1. Stock MCP Tools
* `list_products`: Lists all products in the warehouse along with their current stock levels.
* `search_products`: Searches for products inside the warehouse database.
* `list_out_of_stock` / `list_low_stock`: Lists products with zero stock or below critical thresholds.
* `calculate_replenishment`: Computes the exact quantity needed to restore ideal stock levels.
* `create_incoming_order` / `receive_order`: Registers incoming shipments and accepts them into inventory.

### 2. Marketplace MCP Tools
* `list_sellers`: Lists registered sellers, filtering by rating or delivery time.
* `search_offers`: Searches vendor offers in the marketplace.
* `compare_offers`: Employs multi-criteria decision making (TOPSIS) to rank and select the best offer based on cheapness, speed, seller rating, or balanced scores.
* `create_purchase_draft`: Adds selected offers to a temporary cart (purchase draft).
* `place_order`: Converts approved drafts into active marketplace orders.
* `create_procurement_plan`: Automatically optimizes purchases of multiple missing items across various vendors.

---

## API Endpoints

The Spring Boot backend (`stock-service`) provides the following REST API endpoints:

### Products & Inventory
* `GET /api/products` - Get all products
* `GET /api/products/search?query={q}` - Search products by name, category, or SKU
* `GET /api/products/low-stock` - List low stock products
* `GET /api/products/replenishment` - Compute required replenishment quantities

### Supplier & Incoming Orders
* `GET /api/orders` - List all incoming supplier orders
* `POST /api/orders` - Create a new order to replenish warehouse stock
* `POST /api/orders/{id}/receive` - Mark order as received and increment inventory

### Marketplace Integration
* `GET /api/marketplace/offers/compare?productId={id}` - Rank seller offers using TOPSIS
* `POST /api/marketplace/drafts` - Create a purchase draft
* `POST /api/marketplace/orders` - Finalize a draft into a marketplace purchase order

---

## Future Improvements

* ✅ **Web UI Dashboard:** Completed — responsive React/Vite operations dashboard with stock, procurement, and safe AI execution trace views.
* **Real Marketplace Integrations:** Connect MCP tools to live sandbox APIs of popular e-commerce platforms (Amazon, eBay, local marketplaces).
* **AI Demand Forecasting:** Use historical warehouse data to predict future stock shortages before they drop below critical thresholds.
* **Multi-Agent Negotiation:** Enable a seller agent and buyer agent to dynamically negotiate prices for bulk orders.

---


## Web UI Dashboard (Completed)

The responsive React/Vite dashboard in [`web-ui`](web-ui) provides a Turkish operations interface with:

- independent, fault-tolerant KPI widgets and a stock-health chart;
- searchable/filterable inventory, visual stock levels, and product details;
- real purchase-draft details and an explicit confirmation gate before order creation;
- separate marketplace and incoming replenishment order views;
- an AI Operations Center showing the structured execution plan and observable MCP execution trace (never private chain-of-thought).

### Web UI requirements and configuration

Install Node.js 20+ and copy the environment template. These values are public service locations only; never place secrets in `VITE_*` variables.

```bash
cd web-ui
cp .env.example .env
npm install
```

```dotenv
VITE_API_BASE_URL=http://localhost:8081
VITE_LLM_HOST_URL=http://localhost:8000
```

For browser access, configure allowed origins on the services when needed:

```bash
export CORS_ALLOWED_ORIGINS=http://localhost:5173
export LLM_CORS_ALLOWED_ORIGINS=http://localhost:5173
```

### Recommended startup order and ports

1. Start PostgreSQL, then Spring Boot (`cd stock-service && mvn spring-boot:run`) — **8081**.
2. Start Ollama (`ollama serve`) and ensure `qwen3:8b` is installed — **11434**.
3. Start the web-capable LLM host (`cd llm-host && uvicorn web_api:app --host 0.0.0.0 --port 8000`) — **8000**. The existing `python app.py` CLI remains available.
4. Start the dashboard (`cd web-ui && npm run dev`) — **5173**.

### Quality checks

```bash
cd web-ui
npm run lint
npm run test
npm run build

cd ../stock-service
mvn test

cd ..
python -m unittest discover -s llm-host -p 'test_*.py'
python -m py_compile llm-host/*.py
```

### Dashboard REST endpoints

The dashboard additionally consumes:

- `GET /api/marketplace/drafts` and `GET /api/marketplace/drafts/{draftId}`
- `GET /api/marketplace/orders`
- `GET /api/health`, `POST /api/chat`, conversation detail/confirmation/deletion under the LLM host `/api/conversations/{conversationId}` resource
