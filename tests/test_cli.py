import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mai  # noqa: E402


class MaiCliTest(unittest.TestCase):
    def run_cli(self, db_file, *args):
        output = StringIO()
        with redirect_stdout(output):
            mai.main(["--data", str(db_file), *args])
        return output.getvalue()

    def test_catalog_search_and_stock_management_use_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"

            self.run_cli(
                db_file,
                "merchant",
                "create",
                "--id",
                "seller-a",
                "--name",
                "West Lake Tea",
                "--city",
                "Hangzhou",
                "--service-area",
                "West Lake",
                "--delivery-fee",
                "12",
                "--delivery-eta-minutes",
                "45",
            )
            self.run_cli(
                db_file,
                "product",
                "add",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--title",
                "Longjing Gift Box",
                "--price",
                "88",
                "--stock",
                "5",
                "--tags",
                "longjing,gift",
            )
            search = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            self.assertEqual(search["results"][0]["sku"], "tea-a")
            self.assertEqual(search["results"][0]["delivery"]["eta_minutes"], 45)

            merchants = json.loads(
                self.run_cli(db_file, "search", "merchants", "--query", "west lake", "--city", "Hangzhou", "--format", "json")
            )
            self.assertEqual(merchants["results"][0]["id"], "seller-a")

            self.run_cli(db_file, "product", "stock", "--sku", "tea-a", "--stock", "3")
            updated = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            self.assertEqual(updated["results"][0]["stock"], 3)

            conn = sqlite3.connect(db_file)
            try:
                tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
            finally:
                conn.close()
            self.assertIn("merchants", tables)
            self.assertIn("products", tables)
            self.assertIn("delivery_rules", tables)
            self.assertNotIn("orders", tables)

    def test_merchant_and_product_update_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = Path(tmp) / "mai.sqlite"
            self.run_cli(db_file, "merchant", "create", "--id", "seller-a", "--name", "West Lake Tea")
            self.run_cli(
                db_file,
                "merchant",
                "update",
                "--id",
                "seller-a",
                "--city",
                "Hangzhou",
                "--service-area",
                "West Lake",
                "--contact",
                "wechat:new",
                "--hours",
                "10:00-20:00",
                "--automation-boundaries",
                "Catalog and delivery only.",
                "--delivery-fee",
                "10",
                "--delivery-eta-minutes",
                "30",
            )
            self.run_cli(
                db_file,
                "product",
                "add",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--title",
                "Longjing Gift Box",
                "--price",
                "88",
                "--stock",
                "5",
                "--tags",
                "longjing",
            )
            self.run_cli(
                db_file,
                "product",
                "update",
                "--merchant",
                "seller-a",
                "--sku",
                "tea-a",
                "--price",
                "92",
                "--stock",
                "4",
                "--delivery-attributes",
                "same-city",
            )

            search = json.loads(
                self.run_cli(db_file, "search", "products", "--query", "longjing", "--format", "json")
            )
            product = search["results"][0]
            self.assertEqual(product["price"], 92.0)
            self.assertEqual(product["stock"], 4)
            self.assertEqual(product["merchant"]["contact"], "wechat:new")
            self.assertEqual(product["merchant"]["delivery"]["eta_minutes"], 30)


if __name__ == "__main__":
    unittest.main()
