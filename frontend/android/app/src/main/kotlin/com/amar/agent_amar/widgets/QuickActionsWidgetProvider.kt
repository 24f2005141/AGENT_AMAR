package com.amar.agent_amar.widgets

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.widget.RemoteViews
import com.amar.agent_amar.R
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * QUICK ACTIONS — four shortcuts straight into an existing Sorted route.
 *
 * Purely static: no data, no refresh cost, no state to go stale. A "Sync"
 * button is deliberately NOT included — the backend scheduler already owns
 * continuous Gmail monitoring, so a widget-triggered sync would only add a
 * duplicate, unattributable request path for no user benefit.
 */
class QuickActionsWidgetProvider : HomeWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences
    ) {
        appWidgetIds.forEach { widgetId ->
            val views = RemoteViews(context.packageName, R.layout.widget_quick_actions).apply {
                setOnClickPendingIntent(
                    R.id.quick_inbox,
                    SortedWidgetData.launchIntent(context, "tab", "name=inbox")
                )
                setOnClickPendingIntent(
                    R.id.quick_actions,
                    SortedWidgetData.launchIntent(context, "tab", "name=actions")
                )
                setOnClickPendingIntent(
                    R.id.quick_replies,
                    SortedWidgetData.launchIntent(context, "tab", "name=replies")
                )
                setOnClickPendingIntent(
                    R.id.quick_deadlines,
                    SortedWidgetData.launchIntent(context, "tab", "name=deadlines")
                )
            }
            appWidgetManager.updateAppWidget(widgetId, views)
        }
    }
}
