#!/usr/bin/env python3
"""
CLI test script to verify Odoo XML-RPC connection and retrieve 5 products.
"""
import sys
import os

# Ensure whatsapp-agent root is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.odoo_client import OdooClient, OdooError


def main():
    print("Testing Odoo connection...")
    client = OdooClient()
    try:
        uid = client.authenticate()
        print(f"✅ Authenticated successfully! User ID (UID): {uid}")

        print("Fetching up to 5 products with qty_available...")
        products = client.search_read(
            model="product.product",
            domain=[("sale_ok", "=", True)],
            fields=["id", "name", "list_price", "qty_available"],
            limit=5,
        )

        print(f"Found {len(products)} products:")
        for p in products:
            print(f"- ID: {p.get('id')} | Name: {p.get('name')} | Price: {p.get('list_price')} | Stock: {p.get('qty_available')}")

    except OdooError as e:
        print(f"❌ Odoo Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
