/// <reference path="../pb_data/types.d.ts" />
migrate((db) => {
  const collection = new Collection({
    "id": "pw43ogwdualrq3v",
    "created": "2026-05-28 08:01:16.132Z",
    "updated": "2026-05-28 08:01:16.132Z",
    "name": "team_transfers",
    "type": "base",
    "system": false,
    "schema": [
      {
        "system": false,
        "id": "sduuokiz",
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
        "id": "v5yzqzal",
        "name": "transfers_json",
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
        "id": "jlkg3g0n",
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
      "CREATE UNIQUE INDEX `idx_team_transfers_sofascore_id` ON `team_transfers` (`sofascore_id`)"
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
  const collection = dao.findCollectionByNameOrId("pw43ogwdualrq3v");

  return dao.deleteCollection(collection);
})
