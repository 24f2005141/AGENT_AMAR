class ApiException implements Exception {
  final int statusCode;
  final String message;
  final String? errorType;
  final String? provider;
  final dynamic rawBody;

  ApiException({
    required this.statusCode,
    required this.message,
    this.errorType,
    this.provider,
    this.rawBody,
  });

  bool get isGmailNotConnected =>
      statusCode == 401 || errorType == 'GmailNotConnectedError';

  bool get isNotFound => statusCode == 404;

  bool get isValidationError => statusCode == 422;

  bool get isNetworkError => statusCode == 0;

  @override
  String toString() => 'ApiException($statusCode): $message';

  factory ApiException.fromResponse(int statusCode, dynamic body) {
    String message = 'Unexpected error occurred ($statusCode)';
    String? errorType;
    String? provider;

    if (body is Map<String, dynamic>) {
      errorType = body['error']?.toString();
      provider = body['provider']?.toString();

      final detail = body['detail'];
      if (detail is String) {
        message = detail;
      } else if (detail is List && detail.isNotEmpty) {
        final first = detail.first;
        if (first is Map && first['msg'] != null) {
          final loc = (first['loc'] as List?)?.join('.') ?? '';
          message = '${first['msg']}${loc.isNotEmpty ? ' ($loc)' : ''}';
        } else {
          message = detail.toString();
        }
      } else if (body['message'] is String) {
        message = body['message'];
      }
    } else if (body is String && body.isNotEmpty) {
      message = body;
    }

    return ApiException(
      statusCode: statusCode,
      message: message,
      errorType: errorType,
      provider: provider,
      rawBody: body,
    );
  }

  factory ApiException.networkError(dynamic error) {
    return ApiException(
      statusCode: 0,
      message: 'Unable to connect to AGENT AMAR backend. Please verify FastAPI is running at the configured Base URL.',
      errorType: 'NetworkError',
      rawBody: error.toString(),
    );
  }

  factory ApiException.timeout() {
    return ApiException(
      statusCode: 408,
      message: 'Request timed out while contacting AGENT AMAR backend.',
      errorType: 'Timeout',
    );
  }
}
