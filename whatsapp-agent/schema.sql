-- Schema for WhatsApp AI Customer Support Agent

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(30) UNIQUE NOT NULL,
    name VARCHAR(255),
    odoo_partner_id INT,
    bot_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    wa_message_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_messages_customer_created 
ON messages (customer_id, created_at DESC);
