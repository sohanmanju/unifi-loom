import copy
import json
import re
from pathlib import Path

from .client import validate_path
from .schema import SchemaContract


class Catalog:
    def __init__(self, spec_file="", internal_manifest=""):
        bundled = Path(__file__).parent / "specs/network-10.6.106.json"
        if not bundled.exists():
            bundled = Path(__file__).parents[2] / "specs/network-10.6.106.json"
        self.spec = json.loads(Path(spec_file or bundled).read_text())
        self.contract = SchemaContract(self.spec)
        self.operations = {}
        for path, item in self.spec["paths"].items():
            for method, op in item.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                operation = copy.deepcopy(op)
                operation.update(adapter="official", method=method.upper(),
                                 path="/proxy/network/integration" + path)
                operation["parameters"] = item.get("parameters", []) + op.get("parameters", [])
                self.operations[op["operationId"]] = operation
        self.add_internal("internalListSites", "GET", "/api/self/sites", "List legacy sites")
        for name, route in {
            "Devices": "stat/device", "Clients": "stat/sta", "KnownClients": "list/user",
            "Health": "stat/health", "Networks": "rest/networkconf", "Wifi": "rest/wlanconf",
            "PortProfiles": "rest/portconf", "Routing": "rest/routing",
            "FirewallRules": "rest/firewallrule", "FirewallGroups": "rest/firewallgroup",
            "Settings": "get/setting",
        }.items():
            self.add_internal("internalList" + name, "GET", "/api/s/{site}/" + route, "Legacy " + name)
        for resource in ("networkconf", "wlanconf", "portconf", "routing", "firewallrule", "firewallgroup", "device", "user"):
            for verb, method, suffix in (("Create", "POST", ""), ("Update", "PUT", "/{id}"), ("Delete", "DELETE", "/{id}")):
                if resource in ("device", "user") and verb != "Update":
                    continue
                self.add_internal(f"internal{verb}_{resource}", method,
                                  f"/api/s/{{site}}/rest/{resource}{suffix}", f"Legacy {verb} {resource}")
        for command in ("stamgr", "devmgr", "sitemgr"):
            self.add_internal("internalCommand_" + command, "POST",
                              f"/api/s/{{site}}/cmd/{command}", "Legacy command " + command)
        if internal_manifest:
            for op in json.loads(Path(internal_manifest).read_text()):
                self.add_internal(**op)

    def add_internal(self, operation_id, method, path, summary, body_schema=None):
        if operation_id in self.operations or method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise ValueError("Duplicate operation ID or invalid method in internal manifest")
        self.contract.reachable(body_schema or {})
        full = "/proxy/network" + path
        validate_path(re.sub(r"\{[^}]+\}", "placeholder", full), "internal")
        self.operations[operation_id] = {
            "adapter": "internal", "method": method, "path": full, "summary": summary,
            "parameters": [{"name": name, "in": "path", "required": True,
                            "schema": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"}}
                           for name in re.findall(r"\{([^}]+)\}", full)],
            **({"requestBody": {"required": True, "content": {"application/json": {
                "schema": body_schema or {"type": "object"}}}}} if method in ("POST", "PUT", "PATCH") else {}),
        }

    def validate(self, value, schema):
        self.contract.validate(value, schema)

    def describe(self, operation_id):
        if operation_id not in self.operations:
            raise ValueError("Unknown operation ID; use list_operations")
        op = copy.deepcopy(self.operations[operation_id])
        return {"operation": op, "schemas": copy.deepcopy(self.contract.reachable(op)),
                "requires_change_plan": op["method"] != "GET",
                "compatibility": "official 10.6.106" if op["adapter"] == "official" else "internal route: console validation required"}

    def prepare_request(self, operation_id, path_params=None, query=None, body=None):
        if operation_id not in self.operations:
            raise ValueError("Unknown operation ID")
        op = self.operations[operation_id]
        path_params, query = path_params or {}, query or {}
        params = op.get("parameters", [])
        for kind, values in (("path", path_params), ("query", query)):
            allowed = {p["name"] for p in params if p["in"] == kind}
            if set(values) - allowed:
                raise ValueError("Unknown " + kind + " parameters")
            for p in params:
                if p["in"] != kind:
                    continue
                if p.get("required") and p["name"] not in values:
                    raise ValueError("Missing parameter: " + p["name"])
                if p["name"] in values:
                    self.validate(values[p["name"]], p["schema"])
        path = op["path"]
        for name, value in path_params.items():
            # UUIDs, site names, ports: reject encoded separators and dot segments.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
                raise ValueError("Path parameter contains invalid characters")
            path = path.replace("{" + name + "}", str(value))
        validate_path(path, op["adapter"])
        request_body = op.get("requestBody")
        if body is None and request_body and request_body.get("required"):
            raise ValueError("Request body is required")
        if body is not None:
            if not request_body:
                raise ValueError("This operation does not accept a request body")
            self.validate(body, request_body["content"]["application/json"]["schema"])
        return dict(adapter=op["adapter"], method=op["method"], path=path, query=query, body=body)
