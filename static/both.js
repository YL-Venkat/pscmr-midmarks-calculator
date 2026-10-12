const startButton = document.getElementById("start-button");
if (startButton) {
  startButton.addEventListener("click", () => {
    window.location.href = "calculator.html";
  });
}

const form = document.querySelector('.contact-form');
if (form) {
  const submitBtn = form.querySelector('button[type="submit"]');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    // 1. Email Validation Check
    const emailInput = form.querySelector('input[name="email"]');
    const emailValue = emailInput.value.trim().toLowerCase();

    // Check for '@' and basic structure
    if (!emailValue.includes("@") || !emailValue.includes(".")) {
      alert("Please enter a valid email address.");
      emailInput.focus();
      return;
    }

    const parts = emailValue.split("@");
    const username = parts[0];
    const domain = parts[1];

    // Strictly enforce @gmail.com domain
    if (domain !== "gmail.com") {
      alert("Please enter a valid Gmail address.");
      emailInput.focus();
      return;
    }

    // Block common dummy words or short inputs before @gmail.com
    const dummyWords = ["demo", "test", "example", "sample", "admin", "abc", "xyz", "qwerty", "asdf"];
    const isDummy = dummyWords.some(word => username.includes(word));

    if (isDummy || username.length < 3) {
      alert("Please enter a real Gmail account.");
      emailInput.focus();
      return;
    }

    // 2. Web3Forms Submission
    const formData = new FormData(form);
    formData.append("access_key", "0e7f1b51-ae76-4da8-93f5-ac8b2b115789");

    const originalText = submitBtn.textContent;

    submitBtn.textContent = "Sending...";
    submitBtn.disabled = true;

    try {
      const response = await fetch("https://api.web3forms.com/submit", {
        method: "POST",
        body: formData
      });

      const data = await response.json();

      if (response.ok) {
        alert("Success! Your message has been sent.");
        form.reset();
      } else {
        alert("Error: " + data.message);
      }

    } catch (error) {
      alert("Something went wrong. Please try again.");
    } finally {
      submitBtn.textContent = originalText;
      submitBtn.disabled = false;
    }
  });
}