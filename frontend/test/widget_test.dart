import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:agent_amar/main.dart';
import 'package:agent_amar/services/auth_session_store.dart';
import 'package:agent_amar/services/email_repository.dart';
import 'package:agent_amar/state/auth_controller.dart';
import 'package:agent_amar/state/inbox_controller.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('unauthenticated -> shows the login screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final auth = AuthController(
      repository: MockEmailRepository(),
      store: InMemoryAuthSessionStore(), // no token
      urlLauncher: (_) async => true,
    );
    await auth.bootstrap();

    await tester.pumpWidget(AgentAmarApp(authController: auth, enableNotifications: false));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('Continue with Google'), findsOneWidget);
    expect(find.text('NEEDS ATTENTION'), findsNothing);
  });

  testWidgets('authenticated -> renders the main inbox', (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(() => tester.view.resetPhysicalSize());

    final auth = AuthController(
      repository: MockEmailRepository(),
      store: InMemoryAuthSessionStore('session-token'),
      urlLauncher: (_) async => true,
    );
    await auth.bootstrap();
    expect(auth.isAuthenticated, true);

    final controller = InboxController(
      repository: MockEmailRepository(),
      enableCountdownTimer: false,
    );
    await controller.loadData();

    await tester.pumpWidget(AgentAmarApp(
      authController: auth,
      controller: controller,
      enableNotifications: false,
    ));
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.text('Sorted'), findsOneWidget);
    expect(find.text('NEEDS ATTENTION'), findsOneWidget);
    expect(find.text('UPCOMING DEADLINES'), findsOneWidget);
  });
}
