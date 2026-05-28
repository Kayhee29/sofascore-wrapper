/// <reference path="../pb_data/types.d.ts" />
migrate((db) => {
  const collection = new Collection({
    "id": "sxkbs3m5jj3bwpc",
    "created": "2026-05-28 06:37:27.355Z",
    "updated": "2026-05-28 06:37:27.355Z",
    "name": "leagues",
    "type": "base",
    "system": false,
    "schema": [
      {
        "system": false,
        "id": "oi40styw",
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
        "id": "gy4slx43",
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
        "id": "qdbw0e66",
        "name": "country",
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
        "id": "ktu6dv1b",
        "name": "flag",
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
        "id": "aerdgqvx",
        "name": "logo_url",
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
        "id": "0fayvjsn",
        "name": "logo_file",
        "type": "file",
        "required": false,
        "presentable": false,
        "unique": false,
        "options": {
          "mimeTypes": [
            "image/png",
            "image/jpeg",
            "image/webp",
            "image/svg+xml"
          ],
          "thumbs": null,
          "maxSelect": 1,
          "maxSize": 5242880,
          "protected": false
        }
      },
      {
        "system": false,
        "id": "f6etzbll",
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
        "id": "rmu3v4sw",
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
      "CREATE UNIQUE INDEX `idx_league_sofascore_id` ON `leagues` (`sofascore_id`)"
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
  const collection = dao.findCollectionByNameOrId("sxkbs3m5jj3bwpc");

  return dao.deleteCollection(collection);
})
