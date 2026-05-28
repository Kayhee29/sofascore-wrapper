/// <reference path="../pb_data/types.d.ts" />
migrate((db) => {
  const collection = new Collection({
    "id": "wo2ggy5lfz1dlyh",
    "created": "2026-05-28 06:37:28.165Z",
    "updated": "2026-05-28 06:37:28.165Z",
    "name": "players",
    "type": "base",
    "system": false,
    "schema": [
      {
        "system": false,
        "id": "vkjbttgm",
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
        "id": "qm0mufqo",
        "name": "name",
        "type": "text",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "pattern": ""
        }
      },
      {
        "system": false,
        "id": "xgqagxu0",
        "name": "position",
        "type": "text",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "pattern": ""
        }
      },
      {
        "system": false,
        "id": "vdnh6ban",
        "name": "height",
        "type": "number",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "noDecimal": false
        }
      },
      {
        "system": false,
        "id": "uyztovna",
        "name": "preferredFoot",
        "type": "text",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "pattern": ""
        }
      },
      {
        "system": false,
        "id": "kqwzemtt",
        "name": "marketValue",
        "type": "number",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "noDecimal": false
        }
      },
      {
        "system": false,
        "id": "ryclygbq",
        "name": "avatar_url",
        "type": "text",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "min": null,
          "max": null,
          "pattern": ""
        }
      },
      {
        "system": false,
        "id": "dk5wslki",
        "name": "avatar_file",
        "type": "file",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "mimeTypes": [
            "image/png",
            "image/jpeg",
            "image/webp"
          ],
          "thumbs": null,
          "maxSelect": 1,
          "maxSize": 5242880,
          "protected": false
        }
      },
      {
        "system": false,
        "id": "9rlwkqgh",
        "name": "attributes",
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
        "id": "383qkrsb",
        "name": "raw_json",
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
        "id": "5cyqtuwv",
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
      "CREATE UNIQUE INDEX `idx_player_sofascore_id` ON `players` (`sofascore_id`)"
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
  const collection = dao.findCollectionByNameOrId("wo2ggy5lfz1dlyh");

  return dao.deleteCollection(collection);
})
