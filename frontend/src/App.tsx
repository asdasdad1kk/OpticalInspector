import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import InspectPage from "./pages/InspectPage";
import UploadPage from "./pages/UploadPage";
import DatabasePage from "./pages/DatabasePage";
import ReferencePage from "./pages/ReferencePage";
import SettingsPage from "./pages/SettingsPage";
import ScannerPage from "./pages/ScannerPage";
import ComparePage from "./pages/ComparePage";
export default function App(){
    return <Routes>
        <Route element={<Layout/>}>
        <Route path="/upload" element={<UploadPage/>}/>
        <Route path="/settings" element={<SettingsPage/>}/>
        <Route path="/reference" element={<ReferencePage/>}/>
        <Route path="/inspect" element={<InspectPage/>}/>
        <Route path="/database" element={<DatabasePage/>}/>
        <Route path="/scanner" element={<ScannerPage/>}/>
        <Route path="/compare" element={<ComparePage/>}/>
        <Route path="*" element={<Navigate to="/inspect" replace/>}/>
        </Route>
        </Routes>}
