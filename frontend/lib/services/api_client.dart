import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:http/http.dart' as http;
import '../config/api_config.dart';
import 'api_error.dart';

class ApiClient {
  final http.Client _client;

  /// Returns the current application session bearer token, or null when the
  /// user is not signed in. Injected by [AuthController] so every request the
  /// app makes is automatically authenticated for the current user (Phase 15).
  final String? Function()? tokenProvider;

  /// Invoked exactly once (until the next successful response) when a protected
  /// request comes back "session expired" — i.e. the stored bearer token is
  /// unknown / revoked / expired on the backend. Wired to
  /// [AuthController.onSessionExpired]: clears the token + user state and drops
  /// to the Login screen. Centralised here so no repository needs its own 401
  /// handling, and so a burst of concurrent 401s triggers a single logout.
  final void Function()? onUnauthorized;

  /// Debounce: true after we've reported a session-expiry, reset on any 2xx.
  bool _sessionExpiredReported = false;

  ApiClient({http.Client? client, this.tokenProvider, this.onUnauthorized})
    : _client = client ?? http.Client();

  Uri _buildUri(String path, [Map<String, dynamic>? queryParameters]) {
    final base = ApiConfig.baseUrl.endsWith('/')
        ? ApiConfig.baseUrl.substring(0, ApiConfig.baseUrl.length - 1)
        : ApiConfig.baseUrl;
    final normalizedPath = path.startsWith('/') ? path : '/$path';
    final urlString = '$base$normalizedPath';

    final uri = Uri.parse(urlString);
    if (queryParameters == null || queryParameters.isEmpty) {
      return uri;
    }

    final queryMap = <String, dynamic>{};
    queryParameters.forEach((key, value) {
      if (value != null) {
        queryMap[key] = value.toString();
      }
    });

    return uri.replace(queryParameters: queryMap);
  }

  Map<String, String> _headers() {
    final headers = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    };
    final token = tokenProvider?.call();
    if (token != null && token.isNotEmpty) {
      headers['Authorization'] = 'Bearer $token';
    }
    return headers;
  }

  Future<dynamic> get(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    final uri = _buildUri(path, queryParameters);
    try {
      final response = await _client
          .get(uri, headers: _headers())
          .timeout(ApiConfig.receiveTimeout);
      return _handleResponse(response);
    } on SocketException catch (e) {
      throw ApiException.networkError(e);
    } on TimeoutException {
      throw ApiException.timeout();
    } on http.ClientException catch (e) {
      throw ApiException.networkError(e);
    }
  }

  Future<dynamic> post(
    String path, {
    dynamic body,
    Map<String, dynamic>? queryParameters,
    Duration? timeout,
  }) async {
    final uri = _buildUri(path, queryParameters);
    try {
      final encodedBody = body != null ? jsonEncode(body) : null;
      final response = await _client
          .post(uri, headers: _headers(), body: encodedBody)
          .timeout(timeout ?? ApiConfig.receiveTimeout);
      return _handleResponse(response);
    } on SocketException catch (e) {
      throw ApiException.networkError(e);
    } on TimeoutException {
      throw ApiException.timeout();
    } on http.ClientException catch (e) {
      throw ApiException.networkError(e);
    }
  }

  Future<dynamic> patch(
    String path, {
    dynamic body,
    Map<String, dynamic>? queryParameters,
  }) async {
    final uri = _buildUri(path, queryParameters);
    try {
      final encodedBody = body != null ? jsonEncode(body) : null;
      final response = await _client
          .patch(uri, headers: _headers(), body: encodedBody)
          .timeout(ApiConfig.receiveTimeout);
      return _handleResponse(response);
    } on SocketException catch (e) {
      throw ApiException.networkError(e);
    } on TimeoutException {
      throw ApiException.timeout();
    } on http.ClientException catch (e) {
      throw ApiException.networkError(e);
    }
  }

  Future<dynamic> put(
    String path, {
    dynamic body,
    Map<String, dynamic>? queryParameters,
  }) async {
    final uri = _buildUri(path, queryParameters);
    try {
      final encodedBody = body != null ? jsonEncode(body) : null;
      final response = await _client
          .put(uri, headers: _headers(), body: encodedBody)
          .timeout(ApiConfig.receiveTimeout);
      return _handleResponse(response);
    } on SocketException catch (e) {
      throw ApiException.networkError(e);
    } on TimeoutException {
      throw ApiException.timeout();
    } on http.ClientException catch (e) {
      throw ApiException.networkError(e);
    }
  }

  Future<dynamic> delete(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    final uri = _buildUri(path, queryParameters);
    try {
      final response = await _client
          .delete(uri, headers: _headers())
          .timeout(ApiConfig.receiveTimeout);
      return _handleResponse(response);
    } on SocketException catch (e) {
      throw ApiException.networkError(e);
    } on TimeoutException {
      throw ApiException.timeout();
    } on http.ClientException catch (e) {
      throw ApiException.networkError(e);
    }
  }

  dynamic _handleResponse(http.Response response) {
    dynamic decodedBody;
    if (response.body.isNotEmpty) {
      try {
        decodedBody = jsonDecode(response.body);
      } catch (_) {
        decodedBody = response.body;
      }
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      _sessionExpiredReported = false; // the session is healthy again
      return decodedBody;
    }

    final error = ApiException.fromResponse(response.statusCode, decodedBody);

    // Centralised session-expiry handling. `isAuthExpired` is true only for a
    // missing/invalid/expired app session — NOT for `GmailNotConnectedError`
    // (also 401), which means "reconnect Gmail", not "log out".
    if (error.isAuthExpired &&
        onUnauthorized != null &&
        !_sessionExpiredReported) {
      _sessionExpiredReported = true;
      onUnauthorized!();
    }

    throw error;
  }

  void close() {
    _client.close();
  }
}
