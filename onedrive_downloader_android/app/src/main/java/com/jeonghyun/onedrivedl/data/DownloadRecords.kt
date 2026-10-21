package com.jeonghyun.onedrivedl.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import android.net.Uri

/** 이미 받은 파일 기록. 중간에 끊겨도 받은 파일은 건너뛰고 이어받는다. */
class DownloadRecords(context: Context) :
    SQLiteOpenHelper(context.applicationContext, "records.db", null, 1) {

    data class Record(val uri: Uri, val size: Long)

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE done (item_id TEXT PRIMARY KEY, uri TEXT NOT NULL, size INTEGER NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    fun find(itemId: String): Record? =
        readableDatabase.query("done", arrayOf("uri", "size"), "item_id = ?", arrayOf(itemId), null, null, null)
            .use { c -> if (c.moveToFirst()) Record(Uri.parse(c.getString(0)), c.getLong(1)) else null }

    fun put(itemId: String, uri: Uri, size: Long) {
        val values = ContentValues().apply {
            put("item_id", itemId)
            put("uri", uri.toString())
            put("size", size)
        }
        writableDatabase.insertWithOnConflict("done", null, values, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun count(): Long = readableDatabase.compileStatement("SELECT COUNT(*) FROM done").simpleQueryForLong()

    fun clear() {
        writableDatabase.delete("done", null, null)
    }
}
