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

-- Email agent: customers are identified by phone (WhatsApp) or email (Resend).
-- Written as idempotent ALTERs so an existing database can be upgraded in place.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS email VARCHAR(320);
ALTER TABLE customers ALTER COLUMN phone DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_email
ON customers (email) WHERE email IS NOT NULL;

ALTER TABLE messages ADD COLUMN IF NOT EXISTS email_message_id VARCHAR(255);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_email_message_id
ON messages (email_message_id) WHERE email_message_id IS NOT NULL;

-- A customer must be reachable on at least one channel.
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_phone_or_email;
ALTER TABLE customers ADD CONSTRAINT customers_phone_or_email
CHECK (phone IS NOT NULL OR email IS NOT NULL);
