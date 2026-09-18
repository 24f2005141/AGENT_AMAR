import 'package:flutter/material.dart';
import '../theme/app_theme.dart';

/// The app's first-launch loading screen — shown only while the stored session
/// is being validated (`AuthState.unknown` in `AuthGate`). It disappears the
/// instant that resolves, so it never adds artificial startup delay.
///
/// This is the ONE normal in-app screen that shows the full Sorted mark —
/// everywhere else (navbar, home, cards, login) uses text branding only.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(28),
              child: Image.asset(
                'assets/branding/sorted_mark.png',
                width: 112,
                height: 112,
                fit: BoxFit.cover,
              ),
            ),
            const SizedBox(height: 28),
            Text(
              'Sorted',
              style: AppTheme.brandTitle(fontSize: 32, fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 8),
            Text(
              'Your attention, organized.',
              style: AppTheme.label(fontSize: 13, color: AppColors.textMuted),
            ),
            const SizedBox(height: 36),
            const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.4, color: AppColors.warmBeige),
            ),
          ],
        ),
      ),
    );
  }
}
