import 'dart:convert';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_sign_in/google_sign_in.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Web Client ID from Firebase Console - needed for Google Sign-In v7
const _serverClientId =
    '802718577477-cqm8n897sp24tqgtovdm14kg4c3bualh.apps.googleusercontent.com';

/// User authentication state.
class AuthState {
  final bool isAuthenticated;
  final String? userId;
  final String? email;
  final String? displayName;
  final String? photoUrl;
  final bool isLoading;
  final String? error;

  const AuthState({
    this.isAuthenticated = false,
    this.userId,
    this.email,
    this.displayName,
    this.photoUrl,
    this.isLoading = false,
    this.error,
  });

  AuthState copyWith({
    bool? isAuthenticated,
    String? userId,
    String? email,
    String? displayName,
    String? photoUrl,
    bool? isLoading,
    String? error,
  }) {
    return AuthState(
      isAuthenticated: isAuthenticated ?? this.isAuthenticated,
      userId: userId ?? this.userId,
      email: email ?? this.email,
      displayName: displayName ?? this.displayName,
      photoUrl: photoUrl ?? this.photoUrl,
      isLoading: isLoading ?? this.isLoading,
      error: error,
    );
  }

  Map<String, dynamic> toJson() => {
    'isAuthenticated': isAuthenticated,
    'userId': userId,
    'email': email,
    'displayName': displayName,
    'photoUrl': photoUrl,
  };

  factory AuthState.fromJson(Map<String, dynamic> json) {
    return AuthState(
      isAuthenticated: json['isAuthenticated'] as bool? ?? false,
      userId: json['userId'] as String?,
      email: json['email'] as String?,
      displayName: json['displayName'] as String?,
      photoUrl: json['photoUrl'] as String?,
    );
  }
}

/// Authentication notifier using Firebase Auth with Google Sign-In.
/// Allows consumers to sync files across devices.
/// Providers can use auth to verify chunk storage requests.
class AuthNotifier extends StateNotifier<AuthState> {
  final GoogleSignIn _googleSignIn;
  final FirebaseAuth _firebaseAuth;
  final SharedPreferences _prefs;
  bool _initialized = false;

  static const _authKey = 'firecloud_auth_state';

  AuthNotifier(this._prefs)
    : _googleSignIn = GoogleSignIn.instance,
      _firebaseAuth = FirebaseAuth.instance,
      super(const AuthState()) {
    _loadSavedAuth();
    _initializeAndSetupListeners();
  }

  Future<void> _initializeAndSetupListeners() async {
    // Initialize GoogleSignIn with serverClientId (required for v7 API on Android)
    await _googleSignIn.initialize(serverClientId: _serverClientId);
    _initialized = true;
    _setupAuthListener();
  }

  void _loadSavedAuth() {
    final saved = _prefs.getString(_authKey);
    if (saved != null) {
      try {
        final json = jsonDecode(saved) as Map<String, dynamic>;
        state = AuthState.fromJson(json);
      } catch (_) {
        // Ignore corrupted data
      }
    }
  }

  void _setupAuthListener() {
    // Listen to Firebase Auth state changes
    _firebaseAuth.authStateChanges().listen((User? user) {
      if (user != null) {
        state = AuthState(
          isAuthenticated: true,
          userId: user.uid,
          email: user.email,
          displayName: user.displayName,
          photoUrl: user.photoURL,
          isLoading: false,
        );
        _saveAuth();
      } else if (state.isAuthenticated) {
        _handleSignOut();
      }
    });

    // Listen to Google Sign-In events
    _googleSignIn.authenticationEvents.listen(
      (event) async {
        if (event is GoogleSignInAuthenticationEventSignIn) {
          await _signInToFirebase(event.user);
        }
      },
      onError: (error) {
        if (error is GoogleSignInException &&
            error.code == GoogleSignInExceptionCode.canceled) {
          state = state.copyWith(isLoading: false, error: null);
          return;
        }
        state = state.copyWith(
          isLoading: false,
          error: 'Sign in failed: ${error.toString()}',
        );
      },
    );
  }

  Future<void> _signInToFirebase(GoogleSignInAccount user) async {
    try {
      // Get the idToken from authentication
      final idToken = user.authentication.idToken;

      if (idToken == null) {
        state = state.copyWith(
          isLoading: false,
          error: 'Failed to get ID token from Google',
        );
        return;
      }

      // For Firebase Auth, we can use just the idToken
      // accessToken is optional and only needed for API calls
      final credential = GoogleAuthProvider.credential(idToken: idToken);

      // Sign in to Firebase
      await _firebaseAuth.signInWithCredential(credential);
      // State will be updated by authStateChanges listener
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: 'Firebase sign in failed: ${e.toString()}',
      );
    }
  }

  Future<void> _saveAuth() async {
    await _prefs.setString(_authKey, jsonEncode(state.toJson()));
  }

  void _handleSignOut() {
    state = const AuthState(isAuthenticated: false);
    _prefs.remove(_authKey);
  }

  /// Sign in with Google using Firebase Auth.
  /// Uses GoogleSignIn v7 API with authenticate() method.
  Future<void> signIn() async {
    state = state.copyWith(isLoading: true, error: null);

    try {
      // Wait for initialization if not complete
      while (!_initialized) {
        await Future.delayed(const Duration(milliseconds: 100));
      }

      // Use the v7 API
      if (!_googleSignIn.supportsAuthenticate()) {
        state = state.copyWith(
          isLoading: false,
          error: 'Google Sign-In not supported on this platform',
        );
        return;
      }

      // Trigger authentication - the listener will handle the result
      await _googleSignIn.authenticate();
    } on GoogleSignInException catch (e) {
      if (e.code == GoogleSignInExceptionCode.canceled) {
        state = state.copyWith(isLoading: false, error: null);
        return;
      }
      state = state.copyWith(
        isLoading: false,
        error: 'Sign in failed: ${e.toString()}',
      );
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: 'Sign in failed: ${e.toString()}',
      );
    }
  }

  /// Sign out.
  Future<void> signOut() async {
    state = state.copyWith(isLoading: true);

    try {
      await _googleSignIn.signOut();
      await _firebaseAuth.signOut();
      _handleSignOut();
    } catch (e) {
      state = state.copyWith(
        isLoading: false,
        error: 'Sign out failed: ${e.toString()}',
      );
    }
  }

  /// Get user's unique ID for file ownership.
  String? get ownerId => state.userId;

  /// Fetch a current Firebase ID token for authenticated backend requests.
  Future<String?> getIdToken({bool forceRefresh = false}) async {
    final user = _firebaseAuth.currentUser;
    if (user == null) return null;
    return user.getIdToken(forceRefresh);
  }

  /// Check if files belong to current user.
  bool isOwner(String fileOwnerId) {
    return state.isAuthenticated && state.userId == fileOwnerId;
  }
}

/// Provider for authentication state.
final authProvider = StateNotifierProvider<AuthNotifier, AuthState>((ref) {
  final prefs = ref.watch(sharedPreferencesProvider);
  return AuthNotifier(prefs);
});

/// Provider for SharedPreferences (overridden in main.dart).
final sharedPreferencesProvider = Provider<SharedPreferences>((ref) {
  throw UnimplementedError('sharedPreferencesProvider must be overridden');
});
