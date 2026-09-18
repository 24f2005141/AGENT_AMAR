import 'package:flutter/material.dart';

import '../state/auth_controller.dart';
import '../theme/app_theme.dart';

/// Shown whenever there is no valid application session (Phase 15).
class LoginScreen extends StatelessWidget {
  final AuthController authController;

  const LoginScreen({super.key, required this.authController});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: ListenableBuilder(
          listenable: authController,
          builder: (context, _) {
            final busy = authController.state == AuthState.authenticating;
            // Scroll-and-centre: the form stays vertically centred while there
            // is room, and becomes scrollable the moment there is not - a
            // short screen, a large system font, or (the common case) the
            // keyboard covering half the display. Without this the Column
            // simply overflowed and the button became unreachable.
            return LayoutBuilder(
              builder: (context, constraints) => SingleChildScrollView(
                padding: EdgeInsets.only(
                  bottom: MediaQuery.viewInsetsOf(context).bottom,
                ),
                child: ConstrainedBox(
                  constraints: BoxConstraints(minHeight: constraints.maxHeight),
                  child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 360),
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // The Sorted mark itself is reserved for the splash
                      // screen — the login screen uses text branding only.
                      Text('Sorted',
                          textAlign: TextAlign.center,
                          style: AppTheme.brandTitle(fontSize: 26, fontWeight: FontWeight.w900)),
                      const SizedBox(height: 8),
                      Text('Your attention, organized.',
                          textAlign: TextAlign.center,
                          style: AppTheme.body(fontSize: 13, color: AppColors.textSecondary)),
                      const SizedBox(height: 40),
                      if (authController.errorMessage != null) ...[
                        Container(
                          padding: const EdgeInsets.all(12),
                          decoration: BoxDecoration(
                            color: AppColors.critical.withValues(alpha: 0.14),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: AppColors.critical.withValues(alpha: 0.4)),
                          ),
                          child: Text(authController.errorMessage!,
                              style: AppTheme.body(fontSize: 12, color: AppColors.textPrimary)),
                        ),
                        const SizedBox(height: 16),
                      ],
                      ElevatedButton.icon(
                        onPressed: busy ? null : authController.startGoogleLogin,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: AppColors.warmBeige,
                          foregroundColor: AppColors.textDark,
                          padding: const EdgeInsets.symmetric(vertical: 14),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        ),
                        icon: busy
                            ? const SizedBox(
                                width: 18, height: 18,
                                child: CircularProgressIndicator(
                                    strokeWidth: 2, color: AppColors.textDark))
                            : const Icon(Icons.login, size: 18),
                        label: Text(busy ? 'Signing in…' : 'Continue with Google',
                            style: AppTheme.label(
                                fontSize: 13,
                                fontWeight: FontWeight.bold,
                                color: AppColors.textDark)),
                      ),
                      if (busy) ...[
                        const SizedBox(height: 16),
                        Text(
                          'Complete sign-in with Google — Sorted opens again automatically.',
                          textAlign: TextAlign.center,
                          style: AppTheme.body(fontSize: 11, color: AppColors.textMuted),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}
