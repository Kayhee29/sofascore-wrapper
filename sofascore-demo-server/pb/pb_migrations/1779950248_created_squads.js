/// <reference path="../pb_data/types.d.ts" />
migrate((db) => {
  const collection = new Collection({
    "id": "lbppay0huxi4mjc",
    "created": "2026-05-28 06:37:28.584Z",
    "updated": "2026-05-28 06:37:28.584Z",
    "name": "squads",
    "type": "base",
    "system": false,
    "schema": [
      {
        "system": false,
        "id": "lc8atiha",
        "name": "sofascore_id",
        "type": "text",
        "required": true,
        "presentable": false,
        "unique": false,
        "options": {
          "min": 1,
          "max": null,
          "pattern": ""
        }
      },
      {
        "system": false,
        "id": "lzcm96a7",
        "name": "players_list",
        "type": "json",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "maxSize": 10485760
        }
      },
      {
        "system": false,
        "id": "6ffn0mom",
        "name": "ttl_expired",
        "type": "date",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": "",
          "max": ""
        }
      }
    ],
    "indexes": [
      "CREATE UNIQUE INDEX `idx_squad_sofascore_id` ON `squads` (`sofascore_id`)"
    ],
    "listRule": null,
    "viewRule": null,
    "createRule": null,
    "updateRule": null,
    "deleteRule": null,
    "options": {}
  });

  return Dao(db).saveCollection(collection);
}, (db) => {
  const dao = new Dao(db);
  const collection = dao.findCollectionByNameOrId("lbppay0huxi4mjc");

  return dao.deleteCollection(collection);
})
