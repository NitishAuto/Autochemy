import matplotlib.pyplot as plt

x = [1,2,3,4,5]
y = [10,20,15,25,18]

plt.figure(figsize=(5,3))
plt.plot(x, y)
plt.xlabel("X")
plt.ylabel("Y")
plt.title("Test Plot")
plt.tight_layout()
plt.show()
