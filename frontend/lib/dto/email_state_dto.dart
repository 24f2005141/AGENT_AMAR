class ActionStateDto {
  final String actionRef;
  final String actionType;
  final String? description;
  final bool blocking;
  final String? targetLink;
  final double confidence;
  final String status;
  final DateTime? createdAt;
  final DateTime? completedAt;

  const ActionStateDto({
    required this.actionRef,
    required this.actionType,
    this.description,
    this.blocking = false,
    this.targetLink,
    this.confidence = 0.0,
    this.status = 'PENDING',
    this.createdAt,
    this.completedAt,
  });

  factory ActionStateDto.fromJson(Map<String, dynamic> json) {
    return ActionStateDto(
      actionRef: json['action_ref'] as String? ?? '',
      actionType: json['action_type'] as String? ?? 'OTHER',
      description: json['description'] as String?,
      blocking: json['blocking'] as bool? ?? false,
      targetLink: json['target_link'] as String?,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.0,
      status: json['status'] as String? ?? 'PENDING',
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String)
          : null,
      completedAt: json['completed_at'] != null
          ? DateTime.tryParse(json['completed_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'action_ref': actionRef,
        'action_type': actionType,
        'description': description,
        'blocking': blocking,
        'target_link': targetLink,
        'confidence': confidence,
        'status': status,
        'created_at': createdAt?.toIso8601String(),
        'completed_at': completedAt?.toIso8601String(),
      };
}

class DeadlineStateDto {
  final String deadlineRef;
  final DateTime? deadlineDatetime;
  final String? sourceText;
  final String timezone;
  final bool dateOnly;
  final double confidence;
  final bool isAmbiguous;
  final String? ambiguityReason;
  final bool isPast;
  final String? actionContext;
  final String? relatedActionRef;
  final bool isMonitoring;
  final DateTime? monitoringStartedAt;
  final DateTime? monitoringStoppedAt;

  const DeadlineStateDto({
    required this.deadlineRef,
    this.deadlineDatetime,
    this.sourceText,
    this.timezone = 'UTC',
    this.dateOnly = false,
    this.confidence = 0.0,
    this.isAmbiguous = false,
    this.ambiguityReason,
    this.isPast = false,
    this.actionContext,
    this.relatedActionRef,
    this.isMonitoring = false,
    this.monitoringStartedAt,
    this.monitoringStoppedAt,
  });

  factory DeadlineStateDto.fromJson(Map<String, dynamic> json) {
    return DeadlineStateDto(
      deadlineRef: json['deadline_ref'] as String? ?? '',
      deadlineDatetime: json['deadline_datetime'] != null
          ? DateTime.tryParse(json['deadline_datetime'] as String)
          : null,
      sourceText: json['source_text'] as String?,
      timezone: json['timezone'] as String? ?? 'UTC',
      dateOnly: json['date_only'] as bool? ?? false,
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.0,
      isAmbiguous: json['is_ambiguous'] as bool? ?? false,
      ambiguityReason: json['ambiguity_reason'] as String?,
      isPast: json['is_past'] as bool? ?? false,
      actionContext: json['action_context'] as String?,
      relatedActionRef: json['related_action_ref'] as String?,
      isMonitoring: json['is_monitoring'] as bool? ?? false,
      monitoringStartedAt: json['monitoring_started_at'] != null
          ? DateTime.tryParse(json['monitoring_started_at'] as String)
          : null,
      monitoringStoppedAt: json['monitoring_stopped_at'] != null
          ? DateTime.tryParse(json['monitoring_stopped_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'deadline_ref': deadlineRef,
        'deadline_datetime': deadlineDatetime?.toIso8601String(),
        'source_text': sourceText,
        'timezone': timezone,
        'date_only': dateOnly,
        'confidence': confidence,
        'is_ambiguous': isAmbiguous,
        'ambiguity_reason': ambiguityReason,
        'is_past': isPast,
        'action_context': actionContext,
        'related_action_ref': relatedActionRef,
        'is_monitoring': isMonitoring,
        'monitoring_started_at': monitoringStartedAt?.toIso8601String(),
        'monitoring_stopped_at': monitoringStoppedAt?.toIso8601String(),
      };
}

class AgentTraceEntryDto {
  final String agent;
  final String status;
  final double? confidence;
  final String? method;
  final bool fallbackUsed;
  final int? durationMs;
  final List<String> errorCodes;

  const AgentTraceEntryDto({
    required this.agent,
    required this.status,
    this.confidence,
    this.method,
    this.fallbackUsed = false,
    this.durationMs,
    this.errorCodes = const [],
  });

  factory AgentTraceEntryDto.fromJson(Map<String, dynamic> json) {
    return AgentTraceEntryDto(
      agent: json['agent'] as String? ?? 'Unknown Agent',
      status: json['status'] as String? ?? 'ok',
      confidence: (json['confidence'] as num?)?.toDouble(),
      method: json['method'] as String?,
      fallbackUsed: json['fallback_used'] as bool? ?? false,
      durationMs: json['duration_ms'] as int?,
      errorCodes: (json['error_codes'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'agent': agent,
        'status': status,
        'confidence': confidence,
        'method': method,
        'fallback_used': fallbackUsed,
        'duration_ms': durationMs,
        'error_codes': errorCodes,
      };
}

class ProcessingRunDto {
  final String runId;
  final DateTime processedAt;
  final String status;
  final String pipelineVersion;
  final String finalCategory;
  final String priorityLevel;
  final int priorityScore;
  final bool needsHumanReview;
  final String? summary;
  final List<String> reviewReasons;
  final List<dynamic> conflictsResolved;
  final List<AgentTraceEntryDto> agentTrace;
  final List<dynamic> errors;

  const ProcessingRunDto({
    required this.runId,
    required this.processedAt,
    required this.status,
    this.pipelineVersion = '',
    required this.finalCategory,
    required this.priorityLevel,
    required this.priorityScore,
    this.needsHumanReview = false,
    this.summary,
    this.reviewReasons = const [],
    this.conflictsResolved = const [],
    this.agentTrace = const [],
    this.errors = const [],
  });

  factory ProcessingRunDto.fromJson(Map<String, dynamic> json) {
    return ProcessingRunDto(
      runId: json['run_id'] as String? ?? '',
      processedAt: json['processed_at'] != null
          ? DateTime.parse(json['processed_at'] as String)
          : DateTime.now(),
      status: json['status'] as String? ?? 'ok',
      pipelineVersion: json['pipeline_version'] as String? ?? '',
      finalCategory: json['final_category'] as String? ?? 'OTHER',
      priorityLevel: json['priority_level'] as String? ?? 'LOW',
      priorityScore: json['priority_score'] as int? ?? 0,
      needsHumanReview: json['needs_human_review'] as bool? ?? false,
      summary: json['summary'] as String?,
      reviewReasons: (json['review_reasons'] as List<dynamic>?)
              ?.map((e) => e.toString())
              .toList() ??
          const [],
      conflictsResolved: json['conflicts_resolved'] as List<dynamic>? ?? const [],
      agentTrace: (json['agent_trace'] as List<dynamic>?)
              ?.map((e) => AgentTraceEntryDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      errors: json['errors'] as List<dynamic>? ?? const [],
    );
  }

  Map<String, dynamic> toJson() => {
        'run_id': runId,
        'processed_at': processedAt.toIso8601String(),
        'status': status,
        'pipeline_version': pipelineVersion,
        'final_category': finalCategory,
        'priority_level': priorityLevel,
        'priority_score': priorityScore,
        'needs_human_review': needsHumanReview,
        'summary': summary,
        'review_reasons': reviewReasons,
        'conflicts_resolved': conflictsResolved,
        'agent_trace': agentTrace.map((e) => e.toJson()).toList(),
        'errors': errors,
      };
}

class NotificationStateDto {
  final int id;
  final String notificationType;
  final String severity;
  final String? reminderLevel;
  final bool requiresAlarm;
  final String status;
  final String? detail;
  final int? deadlineId;
  final int? reminderId;
  final DateTime createdAt;
  final DateTime? sentAt;

  const NotificationStateDto({
    required this.id,
    required this.notificationType,
    this.severity = 'NORMAL',
    this.reminderLevel,
    this.requiresAlarm = false,
    required this.status,
    this.detail,
    this.deadlineId,
    this.reminderId,
    required this.createdAt,
    this.sentAt,
  });

  factory NotificationStateDto.fromJson(Map<String, dynamic> json) {
    return NotificationStateDto(
      id: json['id'] as int? ?? 0,
      notificationType: json['notification_type'] as String? ?? 'new_priority_email',
      severity: json['severity'] as String? ?? 'NORMAL',
      reminderLevel: json['reminder_level'] as String?,
      requiresAlarm: json['requires_alarm'] as bool? ?? false,
      status: json['status'] as String? ?? 'PENDING',
      detail: json['detail'] as String?,
      deadlineId: json['deadline_id'] as int?,
      reminderId: json['reminder_id'] as int?,
      createdAt: json['created_at'] != null
          ? DateTime.parse(json['created_at'] as String)
          : DateTime.now(),
      sentAt: json['sent_at'] != null
          ? DateTime.tryParse(json['sent_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'notification_type': notificationType,
        'severity': severity,
        'reminder_level': reminderLevel,
        'requires_alarm': requiresAlarm,
        'status': status,
        'detail': detail,
        'deadline_id': deadlineId,
        'reminder_id': reminderId,
        'created_at': createdAt.toIso8601String(),
        'sent_at': sentAt?.toIso8601String(),
      };
}

class EmailStateOutDto {
  final String emailId;
  final String? threadId;
  final String source;
  final String? senderName;
  final String senderEmail;
  final String subject;
  final String? snippet;
  final DateTime? receivedAt;
  final String finalCategory;
  final double? categoryConfidence;
  final String priorityLevel;
  final int priorityScore;
  final String proximityBucket;
  final bool deadlineIsPast;
  final String? primaryActionType;
  final DateTime? nextDeadlineAt;
  final bool isUnread;
  final bool isViewed;
  final DateTime? viewedAt;
  final bool actionRequired;
  final bool isCompleted;
  final DateTime? completedAt;
  final DateTime? snoozedUntil;
  final bool needsHumanReview;
  final String folderLabel;
  final bool shouldNotify;
  final bool shouldMonitor;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final DateTime? processedAt;

  const EmailStateOutDto({
    required this.emailId,
    this.threadId,
    this.source = 'gmail',
    this.senderName,
    required this.senderEmail,
    required this.subject,
    this.snippet,
    this.receivedAt,
    required this.finalCategory,
    this.categoryConfidence,
    required this.priorityLevel,
    required this.priorityScore,
    this.proximityBucket = 'NONE',
    this.deadlineIsPast = false,
    this.primaryActionType,
    this.nextDeadlineAt,
    this.isUnread = true,
    this.isViewed = false,
    this.viewedAt,
    this.actionRequired = false,
    this.isCompleted = false,
    this.completedAt,
    this.snoozedUntil,
    this.needsHumanReview = false,
    required this.folderLabel,
    this.shouldNotify = false,
    this.shouldMonitor = false,
    this.createdAt,
    this.updatedAt,
    this.processedAt,
  });

  factory EmailStateOutDto.fromJson(Map<String, dynamic> json) {
    return EmailStateOutDto(
      emailId: json['email_id'] as String? ?? '',
      threadId: json['thread_id'] as String?,
      source: json['source'] as String? ?? 'gmail',
      senderName: json['sender_name'] as String?,
      senderEmail: json['sender_email'] as String? ?? '',
      subject: json['subject'] as String? ?? '',
      snippet: json['snippet'] as String?,
      receivedAt: json['received_at'] != null
          ? DateTime.tryParse(json['received_at'] as String)
          : null,
      finalCategory: json['final_category'] as String? ?? 'OTHER',
      categoryConfidence: (json['category_confidence'] as num?)?.toDouble(),
      priorityLevel: json['priority_level'] as String? ?? 'LOW',
      priorityScore: json['priority_score'] as int? ?? 0,
      proximityBucket: json['proximity_bucket'] as String? ?? 'NONE',
      deadlineIsPast: json['deadline_is_past'] as bool? ?? false,
      primaryActionType: json['primary_action_type'] as String?,
      nextDeadlineAt: json['next_deadline_at'] != null
          ? DateTime.tryParse(json['next_deadline_at'] as String)
          : null,
      isUnread: json['is_unread'] as bool? ?? true,
      isViewed: json['is_viewed'] as bool? ?? false,
      viewedAt: json['viewed_at'] != null
          ? DateTime.tryParse(json['viewed_at'] as String)
          : null,
      actionRequired: json['action_required'] as bool? ?? false,
      isCompleted: json['is_completed'] as bool? ?? false,
      completedAt: json['completed_at'] != null
          ? DateTime.tryParse(json['completed_at'] as String)
          : null,
      snoozedUntil: json['snoozed_until'] != null
          ? DateTime.tryParse(json['snoozed_until'] as String)
          : null,
      needsHumanReview: json['needs_human_review'] as bool? ?? false,
      folderLabel: json['folder_label'] as String? ?? 'AMAR/Other',
      shouldNotify: json['should_notify'] as bool? ?? false,
      shouldMonitor: json['should_monitor'] as bool? ?? false,
      createdAt: json['created_at'] != null
          ? DateTime.tryParse(json['created_at'] as String)
          : null,
      updatedAt: json['updated_at'] != null
          ? DateTime.tryParse(json['updated_at'] as String)
          : null,
      processedAt: json['processed_at'] != null
          ? DateTime.tryParse(json['processed_at'] as String)
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
        'email_id': emailId,
        'thread_id': threadId,
        'source': source,
        'sender_name': senderName,
        'sender_email': senderEmail,
        'subject': subject,
        'snippet': snippet,
        'received_at': receivedAt?.toIso8601String(),
        'final_category': finalCategory,
        'category_confidence': categoryConfidence,
        'priority_level': priorityLevel,
        'priority_score': priorityScore,
        'proximity_bucket': proximityBucket,
        'deadline_is_past': deadlineIsPast,
        'primary_action_type': primaryActionType,
        'next_deadline_at': nextDeadlineAt?.toIso8601String(),
        'is_unread': isUnread,
        'is_viewed': isViewed,
        'viewed_at': viewedAt?.toIso8601String(),
        'action_required': actionRequired,
        'is_completed': isCompleted,
        'completed_at': completedAt?.toIso8601String(),
        'snoozed_until': snoozedUntil?.toIso8601String(),
        'needs_human_review': needsHumanReview,
        'folder_label': folderLabel,
        'should_notify': shouldNotify,
        'should_monitor': shouldMonitor,
        'created_at': createdAt?.toIso8601String(),
        'updated_at': updatedAt?.toIso8601String(),
        'processed_at': processedAt?.toIso8601String(),
      };
}

class EmailStateDetailOutDto extends EmailStateOutDto {
  final String? reasoningSummary;
  final List<ActionStateDto> actions;
  final List<DeadlineStateDto> deadlines;
  final List<NotificationStateDto> notifications;
  final ProcessingRunDto? latestProcessing;
  final int processingRunCount;

  const EmailStateDetailOutDto({
    required super.emailId,
    super.threadId,
    super.source,
    super.senderName,
    required super.senderEmail,
    required super.subject,
    super.snippet,
    super.receivedAt,
    required super.finalCategory,
    super.categoryConfidence,
    required super.priorityLevel,
    required super.priorityScore,
    super.proximityBucket,
    super.deadlineIsPast,
    super.primaryActionType,
    super.nextDeadlineAt,
    super.isUnread,
    super.isViewed,
    super.viewedAt,
    super.actionRequired,
    super.isCompleted,
    super.completedAt,
    super.snoozedUntil,
    super.needsHumanReview,
    required super.folderLabel,
    super.shouldNotify,
    super.shouldMonitor,
    super.createdAt,
    super.updatedAt,
    super.processedAt,
    this.reasoningSummary,
    this.actions = const [],
    this.deadlines = const [],
    this.notifications = const [],
    this.latestProcessing,
    this.processingRunCount = 0,
  });

  factory EmailStateDetailOutDto.fromJson(Map<String, dynamic> json) {
    final base = EmailStateOutDto.fromJson(json);
    return EmailStateDetailOutDto(
      emailId: base.emailId,
      threadId: base.threadId,
      source: base.source,
      senderName: base.senderName,
      senderEmail: base.senderEmail,
      subject: base.subject,
      snippet: base.snippet,
      receivedAt: base.receivedAt,
      finalCategory: base.finalCategory,
      categoryConfidence: base.categoryConfidence,
      priorityLevel: base.priorityLevel,
      priorityScore: base.priorityScore,
      proximityBucket: base.proximityBucket,
      deadlineIsPast: base.deadlineIsPast,
      primaryActionType: base.primaryActionType,
      nextDeadlineAt: base.nextDeadlineAt,
      isUnread: base.isUnread,
      isViewed: base.isViewed,
      viewedAt: base.viewedAt,
      actionRequired: base.actionRequired,
      isCompleted: base.isCompleted,
      completedAt: base.completedAt,
      snoozedUntil: base.snoozedUntil,
      needsHumanReview: base.needsHumanReview,
      folderLabel: base.folderLabel,
      shouldNotify: base.shouldNotify,
      shouldMonitor: base.shouldMonitor,
      createdAt: base.createdAt,
      updatedAt: base.updatedAt,
      processedAt: base.processedAt,
      reasoningSummary: json['reasoning_summary'] as String?,
      actions: (json['actions'] as List<dynamic>?)
              ?.map((e) => ActionStateDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      deadlines: (json['deadlines'] as List<dynamic>?)
              ?.map((e) => DeadlineStateDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      notifications: (json['notifications'] as List<dynamic>?)
              ?.map((e) => NotificationStateDto.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      latestProcessing: json['latest_processing'] != null
          ? ProcessingRunDto.fromJson(
              json['latest_processing'] as Map<String, dynamic>)
          : null,
      processingRunCount: json['processing_run_count'] as int? ?? 0,
    );
  }

  @override
  Map<String, dynamic> toJson() {
    final map = super.toJson();
    map['reasoning_summary'] = reasoningSummary;
    map['actions'] = actions.map((e) => e.toJson()).toList();
    map['deadlines'] = deadlines.map((e) => e.toJson()).toList();
    map['notifications'] = notifications.map((e) => e.toJson()).toList();
    map['latest_processing'] = latestProcessing?.toJson();
    map['processing_run_count'] = processingRunCount;
    return map;
  }
}
