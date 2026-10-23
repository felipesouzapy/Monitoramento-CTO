import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db


class ClientePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_file = Path(self.temp_dir.name) / "dados.json"
        self.clients_file = Path(self.temp_dir.name) / "clientes.json"
        self.db_mode_patch = patch("db.is_postgres_connected", return_value=False)
        self.db_mode_patch.start()
        self.json_file_patch = patch("db.JSON_FILE", str(self.data_file))
        self.clients_file_patch = patch("db.CLIENTS_JSON_FILE", str(self.clients_file))
        self.json_file_patch.start()
        self.clients_file_patch.start()

    def tearDown(self):
        self.clients_file_patch.stop()
        self.json_file_patch.stop()
        self.db_mode_patch.stop()
        self.temp_dir.cleanup()

    def write_ctos(self, ctos):
        self.data_file.write_text(json.dumps(ctos), encoding="utf-8")

    def read_ctos(self):
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def test_legacy_customer_is_migrated_and_remains_when_unlinked(self):
        self.write_ctos([{
            "id": 1,
            "nome": "CTO 1",
            "quantidade_portas": "2",
            "portas": [{
                "numero": "1",
                "status": "Ocupada",
                "cliente": "Cliente Antigo",
                "plano": "300 Mega"
            }]
        }])

        ctos = db.db_get_all_ctos()
        cliente = db.db_get_all_clientes()[0]

        self.assertEqual(ctos[0]["portas"][0]["cliente_id"], cliente["id"])
        self.assertEqual(cliente["nome"], "Cliente Antigo")
        self.assertEqual(cliente["plano"], "300 Mega")

        success, message = db.db_desvincular_cliente(cliente["id"])
        self.assertTrue(success, message)
        self.assertEqual(db.db_get_all_clientes()[0]["nome"], "Cliente Antigo")
        self.assertIsNone(db.db_get_all_clientes()[0]["cto_id"])
        self.assertEqual(self.read_ctos()[0]["portas"][0]["status"], "Livre")

    def test_customer_saves_pppoe_and_ppue(self):
        success, message, cliente = db.db_add_cliente(
            "Cliente Novo",
            "500 Mega",
            "usuario@provedor",
            "ppue-123"
        )

        self.assertTrue(success, message)
        saved = db.db_get_all_clientes()[0]
        self.assertEqual(saved["pppoe"], "usuario@provedor")
        self.assertEqual(saved["ppue"], "ppue-123")

    def test_edit_customer_updates_pppoe_and_ppue(self):
        success, message, cliente = db.db_add_cliente("Cliente Editado")
        self.assertTrue(success, message)

        success, message = db.db_update_cliente(
            cliente["id"],
            "Cliente Editado",
            "600 Mega",
            "novo-login",
            "novo-ppue"
        )

        self.assertTrue(success, message)
        updated = db.db_get_all_clientes()[0]
        self.assertEqual(updated["plano"], "600 Mega")
        self.assertEqual(updated["pppoe"], "novo-login")
        self.assertEqual(updated["ppue"], "novo-ppue")

    def test_deleting_port_does_not_delete_customer_record(self):
        self.write_ctos([{
            "id": 1,
            "nome": "CTO 1",
            "quantidade_portas": "1",
            "portas": [{
                "numero": "1",
                "status": "Ocupada",
                "cliente": "Cliente Persistente",
                "plano": "300 Mega"
            }]
        }])
        cliente = db.db_get_all_clientes()[0]

        self.assertTrue(db.db_delete_porta(1, "1"))

        self.assertEqual(db.db_get_all_clientes()[0]["id"], cliente["id"])
        self.assertEqual(db.db_get_all_clientes()[0]["nome"], "Cliente Persistente")
        self.assertEqual(db.db_get_all_clientes()[0]["cto_id"], None)

    def test_customer_cannot_be_linked_to_a_second_cto(self):
        self.write_ctos([
            {
                "id": 1,
                "nome": "CTO 1",
                "quantidade_portas": "2",
                "portas": [{
                    "numero": "1",
                    "status": "Ocupada",
                    "cliente": "Cliente Vinculado",
                    "plano": "300 Mega"
                }]
            },
            {
                "id": 2,
                "nome": "CTO 2",
                "quantidade_portas": "2",
                "portas": []
            }
        ])
        cliente = db.db_get_all_clientes()[0]

        added = db.db_add_porta(2, {
            "numero": "1",
            "status": "Ocupada",
            "cliente": cliente["nome"],
            "cliente_id": cliente["id"],
            "plano": cliente["plano"]
        })

        self.assertFalse(added)
        self.assertEqual(len(self.read_ctos()[1]["portas"]), 0)

    def test_new_cto_cannot_reuse_a_customer_already_linked_elsewhere(self):
        self.write_ctos([{
            "id": 1,
            "nome": "CTO 1",
            "quantidade_portas": "1",
            "portas": [{
                "numero": "1",
                "status": "Ocupada",
                "cliente": "Cliente Vinculado",
                "plano": "300 Mega"
            }]
        }])
        db.db_get_all_clientes()

        cto = db.db_add_cto("CTO duplicada", "1", [{
            "numero": "1",
            "status": "Ocupada",
            "cliente": "Cliente Vinculado",
            "plano": "300 Mega"
        }])

        self.assertIsNone(cto)
        self.assertEqual(len(self.read_ctos()), 1)

    def test_transfer_preserves_customer_credentials_and_assigns_free_port(self):
        self.write_ctos([
            {
                "id": 1,
                "nome": "CTO 1",
                "quantidade_portas": "2",
                "portas": [{
                    "numero": "1",
                    "status": "Ocupada",
                    "cliente": "Cliente A",
                    "plano": "400 Mega"
                }]
            },
            {
                "id": 2,
                "nome": "CTO 2",
                "quantidade_portas": "2",
                "portas": []
            }
        ])
        cliente = db.db_get_all_clientes()[0]
        clients = db.load_json_clients()
        clients[0]["pppoe"] = "login-a"
        clients[0]["ppue"] = "ppue-a"
        db.save_json_clients(clients)

        success, message = db.db_transferir_cliente(cliente["id"], 1, 2, "2")

        self.assertTrue(success, message)
        ctos = self.read_ctos()
        origem = next(cto for cto in ctos if cto["id"] == 1)
        destino = next(cto for cto in ctos if cto["id"] == 2)
        self.assertEqual(origem["portas"][0]["status"], "Livre")
        self.assertEqual(destino["portas"][0]["numero"], "2")
        self.assertEqual(destino["portas"][0]["cliente_id"], cliente["id"])
        saved_cliente = db.db_get_all_clientes()[0]
        self.assertEqual(saved_cliente["pppoe"], "login-a")
        self.assertEqual(saved_cliente["ppue"], "ppue-a")

    def test_transfer_rejects_occupied_destination_without_changes(self):
        self.write_ctos([
            {
                "id": 1,
                "nome": "CTO 1",
                "quantidade_portas": "1",
                "portas": [{
                    "numero": "1",
                    "status": "Ocupada",
                    "cliente": "Cliente A",
                    "plano": "400 Mega"
                }]
            },
            {
                "id": 2,
                "nome": "CTO 2",
                "quantidade_portas": "1",
                "portas": [{
                    "numero": "1",
                    "status": "Ocupada",
                    "cliente": "Outro Cliente",
                    "plano": "200 Mega"
                }]
            }
        ])
        cliente = db.db_get_all_clientes()[0]
        before = self.read_ctos()

        success, message = db.db_transferir_cliente(cliente["id"], 1, 2, "1")

        self.assertFalse(success)
        self.assertEqual(message, "A porta selecionada está ocupada")
        self.assertEqual(self.read_ctos(), before)


if __name__ == "__main__":
    unittest.main()
