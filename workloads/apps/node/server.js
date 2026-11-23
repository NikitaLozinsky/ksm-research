// Простое express-приложение со справочником 200 тыс. записей в куче V8
// (одинаковым у всех экземпляров).
const express = require('express');
const app = express();
const table = new Map();
for (let i = 0; i < 200000; i++) table.set(`key-${i}`, `value-${i}`);
app.get('/', (req, res) => res.json({ n: table.size }));
app.listen(8000, () => console.log('listening 8000'));
