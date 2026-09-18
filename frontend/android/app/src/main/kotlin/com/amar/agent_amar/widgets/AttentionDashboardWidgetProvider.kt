package com.amar.agent_amar.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.view.View
import android.widget.RemoteViews
import com.amar.agent_amar.R
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * ATTENTION DASHBOARD — how much needs attention, by canonical bucket.
 *
 * Counts come straight from the locally published snapshot. They are mutually
 * exclusive by construction (the backend's primary_category), so a Reply
 * Needed email is never also counted as an Action Required one.
 */
class AttentionDashboardWidgetProvider : HomeWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences
    ) {
        val snapshot = SortedWidgetData.snapshot(context)
        val actions = snapshot?.optInt("action_count", 0) ?: 0
        val replies = snapshot?.optInt("reply_count", 0) ?: 0
        val deadlines = snapshot?.optInt("deadline_count", 0) ?: 0
        val nothingPending = actions == 0 && replies == 0 && deadlines == 0

        appWidgetIds.forEach { widgetId ->
            val views = RemoteViews(context.packageName, R.layout.widget_attention_dashboard).apply {
                setTextViewText(R.id.dashboard_action_count, actions.toString())
                setTextViewText(R.id.dashboard_reply_count, replies.toString())
                setTextViewText(R.id.dashboard_deadline_count, deadlines.toString())

                setViewVisibility(
                    R.id.dashboard_counts,
                    if (nothingPending) View.GONE else View.VISIBLE
                )
                setViewVisibility(
                    R.id.dashboard_empty,
                    if (nothingPending) View.VISIBLE else View.GONE
                )

                // Each count opens its own filtered Sorted view.
                setOnClickPendingIntent(
                    R.id.dashboard_actions,
                    SortedWidgetData.launchIntent(context, "tab", "name=actions")
                )
                setOnClickPendingIntent(
                    R.id.dashboard_replies,
                    SortedWidgetData.launchIntent(context, "tab", "name=replies")
                )
                setOnClickPendingIntent(
                    R.id.dashboard_deadlines,
                    SortedWidgetData.launchIntent(context, "tab", "name=deadlines")
                )
                setOnClickPendingIntent(
                    R.id.dashboard_root,
                    SortedWidgetData.launchIntent(context, "tab", "name=inbox")
                )
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }
}
