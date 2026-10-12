// let startTime;
// let timerInterval;

// function startTimer() {
//     startTime = Date.now();
//     const loading = document.getElementById('loading');
//     loading.style.display = 'inline-block';
//     loading.style.color = 'var(--primary)';
//     timerInterval = setInterval(() => {
//         const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
//         loading.innerText = `Processing... (${elapsed}s)`;
//     }, 100);
// }

// function stopTimer() {
//     clearInterval(timerInterval);
//     const elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
//     const mins = Math.floor(elapsedSeconds / 60);
//     const secs = elapsedSeconds % 60;
//     const loading = document.getElementById('loading');
//     loading.style.display = 'inline-block';
//     loading.style.color = '#16a34a';
//     loading.innerText = `Completed in ${mins}m ${secs}s`;
// }

// function previewImage(event) {
//     const reader = new FileReader();
//     reader.onload = function() {
//         const preview = document.getElementById('preview');
//         preview.src = reader.result;
//         preview.style.display = 'block';
//     };
//     if (event.target.files[0]) {
//         reader.readAsDataURL(event.target.files[0]);
//     }
// }

// async function uploadImage() {
//     const fileInput = document.getElementById('imageInput');
//     const container = document.getElementById('resultsContainer');
//     const loading = document.getElementById('loading');
    
//     if (fileInput.files.length === 0) {
//         alert('Please select an image first!');
//         return;
//     }
    
//     const formData = new FormData();
//     formData.append('file', fileInput.files[0]);
//     container.innerHTML = '';
//     startTimer();
    
//     try {
//         const response = await fetch('/process-image', {
//             method: 'POST',
//             body: formData
//         });
//         const data = await response.json();
//         stopTimer();
        
//         if (!response.ok || data.error) {
//             container.innerHTML = `<div style="color:red; text-align:center;">${data.error || 'Failed to process image'}</div>`;
//             return;
//         }

//         data.rows.forEach(subject => {
//             // Bulletproof Pending Check
//             const isMid2Pending = (
//                 subject.Mid2_Score === null || 
//                 subject.Mid2_Score === undefined || 
//                 subject.Mid2_Score === "Pending" ||
//                 String(subject.Mid2_Score).trim() === "" ||
//                 (Number(subject.Mid2_Score) === 0 && (subject.Best_Mid_80 === null || subject.Best_Mid_80 === undefined || subject.Best_Mid_80 === ""))
//             );

//             let cardHTML = '';

//             if (isMid2Pending) {
//                 // --- SCENARIO A: MID 2 IS PENDING ---
//                 const mid1 = Number(subject.Mid1_Score) || 0;
//                 const targetAvg = 16; // Minimum Mid Average needed to reach 40 total with a 24 in Semester
//                 const maxMarks = 30;

//                 let reqMid2 = 0;

//                 // Step 1: Calculate rounded 80% weight assuming Mid 1 is higher
//                 const mid1Weighted80 = Math.floor(mid1 * 0.8 + 0.5);
//                 const neededFromMid2 = targetAvg - mid1Weighted80;

//                 if (neededFromMid2 <= 0) {
//                     reqMid2 = 0;
//                 } else {
//                     // Mid 1 is high enough; Mid 2 only needs 20% weight
//                     reqMid2 = Math.ceil(neededFromMid2 / 0.20);

//                     // If required score exceeds Mid 1, then Mid 2 becomes the 80% weight instead
//                     if (reqMid2 > mid1) {
//                         reqMid2 = Math.ceil((targetAvg - (0.20 * mid1)) / 0.80);
//                     }
//                 }
                
//                 let reqStatusText = '';
//                 let statusPillStyle = '';

//                 if (reqMid2 <= 0) {
//                     reqStatusText = `Mid 1 (${mid1}) is high enough! Mid Average target of ${targetAvg}/30 is already guaranteed.`;
//                     statusPillStyle = 'background: #dcfce7; color: #15803d; border: 1px solid #86efac;';
//                 } else if (reqMid2 > maxMarks) {
//                     reqStatusText = `Cannot reach Mid Avg of ${targetAvg} even with ${maxMarks}/${maxMarks} in Mid 2.`;
//                     statusPillStyle = 'background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5;';
//                 } else {
//                     reqStatusText = `You need at least <strong>${reqMid2}/${maxMarks}</strong> in Mid 2 to reach target Mid Avg of ${targetAvg}.`;
//                     statusPillStyle = 'background: #fef3c7; color: #92400e; border: 1px solid #fcd34d;';
//                 }

//                 cardHTML = `
//                     <div class="subject-card">
//                         <div class="card-header">
//                             Subject: ${subject.Subject}
//                         </div>
//                         <div class="card-body">
//                             <div class="grid-2">
//                                 <div class="metric-box best">
//                                     <div class="box-title">Mid Exam 1 Score</div>
//                                     <div class="box-value">${mid1}</div>
//                                     <div class="box-sub">out of 30</div>
//                                     <span class="badge-best">Completed</span>
//                                 </div>
//                                 <div class="metric-box">
//                                     <div class="box-title">Mid Exam 2 Score</div>
//                                     <div class="box-value" style="color: #d97706;">Pending</div>
//                                     <div class="box-sub">out of 30</div>
//                                 </div>
//                             </div>
//                             <div class="banner-blue" style="margin-bottom: 16px;">
//                                 <div class="banner-title">Required Mid 2 Score (for ${targetAvg} Mid Avg)</div>
//                                 <div class="banner-val">${reqMid2 <= maxMarks ? (reqMid2 > 0 ? reqMid2 : 0) : 'N/A'}</div>
//                                 <div class="banner-sub">out of 30</div>
//                             </div>
//                             <div class="status-pill" style="${statusPillStyle}">
//                                 ${reqStatusText}
//                             </div>
//                         </div>
//                     </div>
//                 `;
//             } else {
//                 // --- SCENARIO B: BOTH MIDS COMPLETED ---
//                 const mid1Val = Number(subject.Mid1_Score) || 0;
//                 const mid2Val = Number(subject.Mid2_Score) || 0;
//                 const mid1IsBest = mid1Val >= mid2Val;
                
//                 const isGreen = subject.Required_Sem_Marks === 24;
//                 const boxStyle = isGreen 
//                     ? 'background-color: #dcfce7; border: 2px solid #16a34a; color: #15803d;' 
//                     : 'background-color: #fee2e2; border: 2px solid #dc2626; color: #b91c1c;';
//                 const textStyle = 'font-weight: bold;';

//                 cardHTML = `
//                     <div class="subject-card">
//                         <div class="card-header">
//                              Subject: ${subject.Subject}
//                         </div>
//                         <div class="card-body">
//                             <div class="grid-2">
//                                 <div class="metric-box ${mid1IsBest ? 'best' : ''}">
//                                     <div class="box-title">Mid Exam 1 Score</div>
//                                     <div class="box-value">${subject.Mid1_Score}</div>
//                                     <div class="box-sub">out of 30</div>
//                                     ${mid1IsBest ? '<span class="badge-best">Best</span>' : ''}
//                                 </div>
//                                 <div class="metric-box ${!mid1IsBest ? 'best' : ''}">
//                                     <div class="box-title">Mid Exam 2 Score</div>
//                                     <div class="box-value">${subject.Mid2_Score}</div>
//                                     <div class="box-sub">out of 30</div>
//                                     ${!mid1IsBest ? '<span class="badge-best">Best</span>' : ''}
//                                 </div>
//                             </div>
//                             <div class="weightage-banner">
//                                 <div class="w-item">
//                                     <span class="w-label">Best Mid (80%)</span>
//                                     <span class="w-val">${subject.Best_Mid_80}</span>
//                                 </div>
//                                 <div class="w-item" style="text-align: right;">
//                                     <span class="w-label">Other Mid (20%)</span>
//                                     <span class="w-val">${subject.Other_Mid_20}</span>
//                                 </div>
//                             </div>
//                             <div class="grid-2">
//                                 <div class="banner-blue">
//                                     <div class="banner-title">Final Mid Average</div>
//                                     <div class="banner-val">${subject.Final_Mid_Average}</div>
//                                     <div class="banner-sub">out of 30</div>
//                                 </div>
//                                 <div style="${boxStyle}; border-radius: 12px; padding: 20px;">
//                                     <div class="banner-title" style="${textStyle}">Required Semester Marks</div>
//                                     <div class="banner-val" style="${textStyle}">${subject.Required_Sem_Marks}</div>
//                                     <div class="banner-sub" style="${textStyle}">out of 70</div>
//                                 </div>
//                             </div>
//                             <div class="status-pill">
//                                 You need ${subject.Required_Sem_Marks} marks in semester exam to pass.
//                             </div>
//                         </div>
//                     </div>
//                 `;
//             }

//             container.innerHTML += cardHTML;
//         });
//     } catch (error) {
//         stopTimer();
//         loading.style.color = '#dc2626';
//         loading.innerText = 'Failed to connect';
//         container.innerHTML = `<div style="color:red; text-align:center;">Error connecting to server.</div>`;
//     }
// }
let startTime;
let timerInterval;

function startTimer() {
    startTime = Date.now();
    const loading = document.getElementById('loading');
    loading.style.display = 'inline-block';
    loading.style.color = 'var(--primary)';
    timerInterval = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        loading.innerText = `Processing... (${elapsed}s)`;
    }, 100);
}

function stopTimer() {
    clearInterval(timerInterval);
    const elapsedSeconds = Math.floor((Date.now() - startTime) / 1000);
    const mins = Math.floor(elapsedSeconds / 60);
    const secs = elapsedSeconds % 60;
    const loading = document.getElementById('loading');
    loading.style.display = 'inline-block';
    loading.style.color = '#16a34a';
    loading.innerText = `Completed in ${mins}m ${secs}s`;
}

function previewImage(event) {
    const reader = new FileReader();
    reader.onload = function() {
        const preview = document.getElementById('preview');
        preview.src = reader.result;
        preview.style.display = 'block';
    };
    if (event.target.files[0]) {
        reader.readAsDataURL(event.target.files[0]);
    }
}

async function uploadImage() {
    const fileInput = document.getElementById('imageInput');
    const container = document.getElementById('resultsContainer');
    const loading = document.getElementById('loading');
    
    if (fileInput.files.length === 0) {
        alert('Please select an image first!');
        return;
    }
    
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    container.innerHTML = '';
    startTimer();
    
    try {
        const response = await fetch('/process-image', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        stopTimer();
        
        if (!response.ok || data.error) {
            container.innerHTML = `<div style="color:red; text-align:center;">${data.error || 'Failed to process image'}</div>`;
            return;
        }

        data.rows.forEach(subject => {
            // Bulletproof Pending Check
            const isMid2Pending = (
                subject.Mid2_Score === null || 
                subject.Mid2_Score === undefined || 
                subject.Mid2_Score === "Pending" ||
                String(subject.Mid2_Score).trim() === "" ||
                (Number(subject.Mid2_Score) === 0 && (subject.Best_Mid_80 === null || subject.Best_Mid_80 === undefined || subject.Best_Mid_80 === ""))
            );

            let cardHTML = '';

            if (isMid2Pending) {
                // --- SCENARIO A: MID 2 IS PENDING ---
                const mid1 = Number(subject.Mid1_Score) || 0;
                const targetAvg = 16; // Minimum Mid Average needed to reach 40 total with a 24 in Semester
                const maxMarks = 30;

                let reqMid2 = 0;

                // Step 1: Calculate rounded 80% weight assuming Mid 1 is higher
                const mid1Weighted80 = Math.floor(mid1 * 0.8 + 0.5);
                const neededFromMid2 = targetAvg - mid1Weighted80;

                if (neededFromMid2 <= 0) {
                    reqMid2 = 0;
                } else {
                    // Mid 1 is high enough; Mid 2 only needs 20% weight
                    reqMid2 = Math.ceil(neededFromMid2 / 0.20);

                    // If required score exceeds Mid 1, then Mid 2 becomes the 80% weight instead
                    if (reqMid2 > mid1) {
                        reqMid2 = Math.ceil((targetAvg - (0.20 * mid1)) / 0.80);
                    }
                }
                
                let reqStatusText = '';
                let statusPillStyle = '';

                if (reqMid2 <= 0) {
                    reqStatusText = `Mid 1 (${mid1}) is high enough! Mid Average target of ${targetAvg}/30 is already guaranteed.`;
                    statusPillStyle = 'background: #dcfce7; color: #15803d; border: 1px solid #86efac;';
                } else if (reqMid2 > maxMarks) {
                    reqStatusText = `Cannot reach Mid Avg of ${targetAvg} even with ${maxMarks}/${maxMarks} in Mid 2.`;
                    statusPillStyle = 'background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5;';
                } else {
                    reqStatusText = `You need at least <strong>${reqMid2}/${maxMarks}</strong> in Mid 2 to reach target Mid Avg of ${targetAvg}.`;
                    statusPillStyle = 'background: #fef3c7; color: #92400e; border: 1px solid #fcd34d;';
                }

                cardHTML = `
                    <div class="subject-card">
                        <div class="card-header">
                            Subject: ${subject.Subject}
                        </div>
                        <div class="card-body">
                            <div class="grid-2">
                                <div class="metric-box best">
                                    <div class="box-title">Mid Exam 1 Score</div>
                                    <div class="box-value">${mid1}</div>
                                    <div class="box-sub">out of 30</div>
                                    <span class="badge-best">Completed</span>
                                </div>
                                <div class="metric-box">
                                    <div class="box-title">Mid Exam 2 Score</div>
                                    <div class="box-value" style="color: #d97706;">Pending</div>
                                    <div class="box-sub">out of 30</div>
                                </div>
                            </div>
                            <div class="banner-blue" style="margin-bottom: 16px;">
                                <div class="banner-title">Required Mid 2 Score (for ${targetAvg} Mid Avg)</div>
                                <div class="banner-val">${reqMid2 <= maxMarks ? (reqMid2 > 0 ? reqMid2 : 0) : 'N/A'}</div>
                                <div class="banner-sub">out of 30</div>
                            </div>
                            <div class="status-pill" style="${statusPillStyle}">
                                ${reqStatusText}
                            </div>
                        </div>
                    </div>
                `;
            } else {
                // --- SCENARIO B: BOTH MIDS COMPLETED ---
                const mid1Val = Number(subject.Mid1_Score) || 0;
                const mid2Val = Number(subject.Mid2_Score) || 0;
                const mid1IsBest = mid1Val >= mid2Val;
                
                const isGreen = subject.Required_Sem_Marks === 24;
                const boxStyle = isGreen 
                    ? 'background-color: #dcfce7; border: 2px solid #16a34a; color: #15803d;' 
                    : 'background-color: #fee2e2; border: 2px solid #dc2626; color: #b91c1c;';
                const textStyle = 'font-weight: bold;';

                cardHTML = `
                    <div class="subject-card">
                        <div class="card-header">
                             Subject: ${subject.Subject}
                        </div>
                        <div class="card-body">
                            <div class="grid-2">
                                <div class="metric-box ${mid1IsBest ? 'best' : ''}">
                                    <div class="box-title">Mid Exam 1 Score</div>
                                    <div class="box-value">${subject.Mid1_Score}</div>
                                    <div class="box-sub">out of 30</div>
                                    ${mid1IsBest ? '<span class="badge-best">Best</span>' : ''}
                                </div>
                                <div class="metric-box ${!mid1IsBest ? 'best' : ''}">
                                    <div class="box-title">Mid Exam 2 Score</div>
                                    <div class="box-value">${subject.Mid2_Score}</div>
                                    <div class="box-sub">out of 30</div>
                                    ${!mid1IsBest ? '<span class="badge-best">Best</span>' : ''}
                                </div>
                            </div>
                            <div class="weightage-banner">
                                <div class="w-item">
                                    <span class="w-label">Best Mid (80%)</span>
                                    <span class="w-val">${subject.Best_Mid_80}</span>
                                </div>
                                <div class="w-item" style="text-align: right;">
                                    <span class="w-label">Other Mid (20%)</span>
                                    <span class="w-val">${subject.Other_Mid_20}</span>
                                </div>
                            </div>
                            <div class="grid-2">
                                <div class="banner-blue">
                                    <div class="banner-title">Final Mid Average</div>
                                    <div class="banner-val">${subject.Final_Mid_Average}</div>
                                    <div class="banner-sub">out of 30</div>
                                </div>
                                <div style="${boxStyle}; border-radius: 12px; padding: 20px;">
                                    <div class="banner-title" style="${textStyle}">Required Semester Marks</div>
                                    <div class="banner-val" style="${textStyle}">${subject.Required_Sem_Marks}</div>
                                    <div class="banner-sub" style="${textStyle}">out of 70</div>
                                </div>
                            </div>
                            <div class="status-pill">
                                You need ${subject.Required_Sem_Marks} marks in semester exam to pass.
                            </div>
                        </div>
                    </div>
                `;
            }

            container.innerHTML += cardHTML;
        });
    } catch (error) {
        stopTimer();
        loading.style.color = '#dc2626';
        loading.innerText = 'Failed to connect';
        container.innerHTML = `<div style="color:red; text-align:center;">Error connecting to server.</div>`;
    }
}
