package com.amar.agent_amar.widgets

import android.content.Context
import android.net.Uri
import android.app.PendingIntent
import com.amar.agent_amar.MainActivity
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetPlugin
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Reads the snapshot the Flutter app publishes (see HomeWidgetService) out of
 * the shared preference store home_widget writes to.
 *
 * The widgets are deliberately dumb renderers: every "what deserves attention"
 * decision was already made in Dart and written here. Nothing in this package
 * ever performs network I/O — no FastAPI, no Gmail, no push, no polling.
 */
object SortedWidgetData {

    const val SNAPSHOT_KEY = "sorted_widget_snapshot"

    /** Deep-link host used by all widget taps (see AndroidManifest). */
    private const val SCHEME = "agentamar"
    private const val HOST = "widget"

    fun snapshot(context: Context): JSONObject? {
        return try {
            val raw = HomeWidgetPlugin.getData(context).getString(SNAPSHOT_KEY, null)
            if (raw.isNullOrEmpty()) null else JSONObject(raw)
        } catch (e: Exception) {
            null
        }
    }

    fun optObject(parent: JSONObject?, key: String): JSONObject? {
        if (parent == null || parent.isNull(key)) return null
        return parent.optJSONObject(key)
    }

    /** Parses an ISO-8601 UTC timestamp written by Dart's toIso8601String(). */
    fun parseUtc(value: String?): Date? {
        if (value.isNullOrEmpty()) return null
        val patterns = arrayOf("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", "yyyy-MM-dd'T'HH:mm:ss'Z'")
        for (pattern in patterns) {
            try {
                val fmt = SimpleDateFormat(pattern, Locale.US)
                fmt.timeZone = TimeZone.getTimeZone("UTC")
                return fmt.parse(value)
            } catch (_: Exception) {
                // try the next pattern
            }
        }
        return null
    }

    /** "Today, 5:00 PM" / "Tomorrow · 10:00 AM" / "Mon, 12 Sep · 9:00 AM". */
    fun formatWhen(date: Date): String {
        val time = SimpleDateFormat("h:mm a", Locale.getDefault()).format(date)
        val dayFmt = SimpleDateFormat("yyyyMMdd", Locale.getDefault())
        val today = dayFmt.format(Date())
        val tomorrow = dayFmt.format(Date(System.currentTimeMillis() + 86_400_000L))
        return when (dayFmt.format(date)) {
            today -> "Today · $time"
            tomorrow -> "Tomorrow · $time"
            else -> SimpleDateFormat("EEE, d MMM", Locale.getDefault()).format(date) + " · " + time
        }
    }

    /**
     * A PendingIntent that launches the Flutter app on a widget deep link.
     * Routing itself stays in the app's existing navigation — the widgets do
     * not own a second navigation architecture.
     */
    fun launchIntent(context: Context, path: String, query: String? = null): PendingIntent {
        val uriString = buildString {
            append("$SCHEME://$HOST/$path")
            if (!query.isNullOrEmpty()) append("?$query")
        }
        return HomeWidgetLaunchIntent.getActivity(
            context,
            MainActivity::class.java,
            Uri.parse(uriString)
        )
    }
}
