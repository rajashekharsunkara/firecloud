import { getApp, getApps, initializeApp } from 'firebase/app';
import {
	GoogleAuthProvider,
	browserLocalPersistence,
	getAuth,
	setPersistence,
} from 'firebase/auth';

const firebaseConfig = {
	apiKey: 'AIzaSyBbDFpcOr_dz52rLv9SL8arRUlTgm5SF5Y',
	authDomain: 'firecloud-d1d99.firebaseapp.com',
	projectId: 'firecloud-d1d99',
	storageBucket: 'firecloud-d1d99.firebasestorage.app',
	messagingSenderId: '802718577477',
	appId: '1:802718577477:web:87d70d532cb88d4b88842c',
	measurementId: 'G-7PKTFQ6160',
};

const firebaseApp = getApps().length ? getApp() : initializeApp(firebaseConfig);
const firebaseAuth = getAuth(firebaseApp);

// Keep auth sessions persisted for desktop relaunches.
setPersistence(firebaseAuth, browserLocalPersistence).catch(() => {
	// Ignore persistence failures and fall back to in-memory auth.
});

const googleProvider = new GoogleAuthProvider();
googleProvider.addScope('email');
googleProvider.addScope('profile');

export { firebaseApp, firebaseAuth, googleProvider };
